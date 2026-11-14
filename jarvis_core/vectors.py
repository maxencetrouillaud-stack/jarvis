"""Mémoire vectorielle : index de voisinage approché, reclassement et regroupement.

Bibliothèque standard uniquement (numpy est utilisé s'il est installé, sans être nécessaire).

- Signatures binaires (hachage sensible à la localité, projections aléatoires creuses) : 62 bits par vecteur.
  Deux vecteurs proches ont des signatures proches (distance de Hamming faible). Une signature tient dans
  un entier SQLite : l'index tient en mémoire même pour des millions de passages (16 octets par passage).
- Recherche approchée : on compare la signature de la question à toutes les signatures (opération sur des
  entiers, rapide), on garde les meilleurs candidats, puis on calcule le cosinus exact sur ces seuls candidats.
- Reclassement MMR : évite de renvoyer cinq passages qui disent la même chose.
- K-moyennes sphériques sur des projections réduites : « zones sémantiques » affichées dans la sphère.
"""
import array
import heapq
import math
import operator
import threading
import zlib

BITS = 62

try:  # accélération facultative
    import numpy as _np  # noqa: F401
except ImportError:
    _np = None

_planes_cache = {}
_proj_cache = {}
_lock = threading.Lock()


def _rng(seed):
    x = zlib.crc32(seed.encode()) or 1
    while True:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        yield x


def _sparse(dim, n, seed):
    """n projections creuses (Achlioptas) : chaque coordonnée vaut +1 (1/6), -1 (1/6) ou 0 (2/3)."""
    key = (dim, n, seed)
    with _lock:
        if key in _planes_cache:
            return _planes_cache[key]
        r = _rng(f"{seed}-{dim}")
        out = []
        for _ in range(n):
            plus, minus = [], []
            for i in range(dim):
                v = next(r) % 6
                if v == 0:
                    plus.append(i)
                elif v == 1:
                    minus.append(i)
            plus = plus or [0]
            minus = minus or [dim - 1]
            out.append((operator.itemgetter(*plus) if len(plus) > 1 else (lambda v, i=plus[0]: (v[i],)),
                        operator.itemgetter(*minus) if len(minus) > 1 else (lambda v, i=minus[0]: (v[i],))))
        _planes_cache[key] = out
        return out


def signature(vec):
    """Signature binaire (entier positif sur 62 bits) d'un vecteur, ou None."""
    if not vec:
        return None
    s = 0
    for b, (p, m) in enumerate(_sparse(len(vec), BITS, "lsh")):
        if sum(p(vec)) - sum(m(vec)) > 0:
            s |= 1 << b
    return s


def project(vec, dims=32):
    """Projection réelle réduite (pour le regroupement)."""
    if not vec:
        return None
    out = [sum(p(vec)) - sum(m(vec)) for p, m in _sparse(len(vec), dims, "proj")]
    n = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / n for x in out]


if hasattr(int, "bit_count"):
    def popcount(x):
        return x.bit_count()
else:  # Python 3.9
    def popcount(x):
        return bin(x).count("1")


class SigIndex:
    """Index en mémoire (identifiant, signature), rechargé quand la table change."""

    def __init__(self):
        self.key = None
        self.ids = array.array("q")
        self.sigs = array.array("q")
        self.lock = threading.Lock()

    def ensure(self, key, loader):
        with self.lock:
            if key == self.key:
                return
            ids, sigs = array.array("q"), array.array("q")
            for i, s in loader():
                if s is not None:
                    ids.append(i)
                    sigs.append(s)
            self.ids, self.sigs, self.key = ids, sigs, key

    def add(self, i, sig):
        """Ajoute une entrée à un index déjà chargé (sinon elle sera lue au prochain chargement)."""
        with self.lock:
            if self.key is not None and sig is not None:
                self.ids.append(i)
                self.sigs.append(sig)

    def invalidate(self):
        with self.lock:
            self.key = None

    def __len__(self):
        return len(self.ids)

    def nearest(self, qsig, n=300):
        if qsig is None or not self.ids:
            return []
        ids, sigs = self.ids, self.sigs
        best = heapq.nsmallest(n, range(len(ids)), key=lambda k: popcount(qsig ^ sigs[k]))
        return [ids[k] for k in best]


def dot(a, b):
    return sum(map(operator.mul, a, b))


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    na, nb = math.sqrt(dot(a, a)), math.sqrt(dot(b, b))
    return dot(a, b) / (na * nb) if na and nb else 0.0


def mmr(items, vec_of, relevance, k, lam=0.75):
    """Sélection gloutonne pertinence / diversité (Maximal Marginal Relevance)."""
    pool = list(items)
    chosen, cvecs = [], []
    while pool and len(chosen) < k:
        best, best_s = None, -1e9
        for it in pool:
            v = vec_of(it)
            red = max((cosine(v, c) for c in cvecs), default=0.0) if v is not None else 0.0
            s = lam * relevance(it) - (1 - lam) * red
            if s > best_s:
                best, best_s = it, s
        chosen.append(best)
        pool.remove(best)
        v = vec_of(best)
        if v is not None:
            cvecs.append(v)
    return chosen


def kmeans(vectors, k, iters=8, seed="zones"):
    """K-moyennes sphériques (vecteurs normalisés). -> (affectations, centres)"""
    n = len(vectors)
    if n == 0 or k <= 0:
        return [], []
    k = min(k, n)
    r = _rng(seed)
    centers = [list(vectors[next(r) % n])]
    while len(centers) < k:  # k-means++ déterministe : le point le plus éloigné des centres déjà choisis
        far, far_d = 0, -1.0
        for i, v in enumerate(vectors):
            d = 1 - max(dot(v, c) for c in centers)
            if d > far_d:
                far, far_d = i, d
        centers.append(list(vectors[far]))
    assign = [0] * n
    for _ in range(iters):
        changed = False
        for i, v in enumerate(vectors):
            best = max(range(k), key=lambda c: dot(v, centers[c]))
            if best != assign[i]:
                assign[i], changed = best, True
        dim = len(vectors[0])
        sums = [[0.0] * dim for _ in range(k)]
        for i, v in enumerate(vectors):
            s = sums[assign[i]]
            for j in range(dim):
                s[j] += v[j]
        for c in range(k):
            nrm = math.sqrt(sum(x * x for x in sums[c]))
            if nrm:
                centers[c] = [x / nrm for x in sums[c]]
        if not changed:
            break
    return assign, centers
