// Test de l'interface dans un vrai navigateur (Chromium via Playwright), sur les faux fournisseurs.
//   node tests/e2e.mjs [dossier_captures]
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const shots = process.argv[2];
const py = spawn("python3", ["tests/demo_server.py"], { stdio: ["ignore", "pipe", "inherit"] });
const [port, webUrl, comfyUrl] = await new Promise((res) => py.stdout.once("data", (d) => res(String(d).trim().split(/\s+/))));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1320, height: 860 }, deviceScaleFactor: shots ? 2 : 1 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("dialog", (d) => d.accept(d.type() === "prompt" ? "Titre renommé" : undefined));
// Reconnaissance et synthèse vocales simulées : le navigateur de test n'a ni micro ni service de transcription.
await page.addInitScript(() => {
  const V = (window.__voice = { cur: null, spoken: [], cancelled: 0, slow: false, timers: [] });
  class FakeSR { start() { V.cur = this; } abort() {} stop() {} }
  window.SpeechRecognition = FakeSR; window.webkitSpeechRecognition = FakeSR;
  window.__say = (text, final = true) => V.cur.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: final })] });
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: {
    speak(u) { V.spoken.push(u.text); u.onstart && u.onstart(); V.timers.push(setTimeout(() => u.onend && u.onend(), V.slow ? 6000 : 40)); },
    cancel() { V.cancelled++; V.timers.forEach(clearTimeout); V.timers = []; },
    getVoices() { return []; }, addEventListener() {}, removeEventListener() {},
  } });
});
const sharp = require("sharp");
const shot = async (n) => shots && page.screenshot({ path: `${shots}/${n}.png` });
const step = (s) => console.log("✓", s);
const nav = (label) => page.locator(".rail-btn", { has: page.locator("span", { hasText: new RegExp(`^${label}$`) }) });

try {
  await page.goto(`http://127.0.0.1:${port}/`);

  // --- configuration initiale
  await page.getByText("Configuration initiale").waitFor();
  await page.getByLabel("Comment s'appelle votre assistant ?").fill("Jarvis");
  await page.getByLabel("Et vous, quel est votre prénom ?").fill("Maxence");
  await page.getByRole("button", { name: "Continuer" }).click();
  await page.getByText("Connecter un premier modèle").waitFor();
  await shot("1-configuration");
  await page.getByRole("button", { name: "Enregistrer et tester" }).isDisabled().then((d) => assert.ok(d, "bouton actif sans clé"));
  await page.getByLabel(/Clé API/).fill("mauvaise-cle");
  await page.getByRole("button", { name: "Enregistrer et tester" }).click();
  await page.locator(".toast-error").filter({ hasText: "401" }).waitFor();
  step("une mauvaise clé est refusée avec le message du fournisseur");
  await page.getByLabel(/Clé API/).fill("cle-test");
  await page.getByRole("button", { name: "Enregistrer et tester" }).click();
  await page.getByText(/Connecté : Claude/).waitFor();
  await page.getByRole("button", { name: "Terminer" }).click();
  await page.getByRole("heading", { name: "Bonjour Maxence." }).waitFor();
  step("configuration initiale et connexion d'un fournisseur");

  // --- conversation, flux, mémoire automatique
  const box = page.getByLabel("Message", { exact: true });
  await box.fill("Salut ! Je suis étudiant en BUT GMP à Limoges.");
  await box.press("Enter");
  await page.locator(".msg.assistant .md pre code").waitFor();
  await page.locator(".toast-memory").filter({ hasText: "BUT GMP" }).waitFor();
  assert.equal(await page.locator(".msg.assistant .md li").count(), 2);
  step("réponse en flux, rendu Markdown, souvenir appris automatiquement");

  // --- changement de modèle sans perte de mémoire + HTML neutralisé
  await page.locator(".composer select").selectOption("ollama:llama-test:8b");
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await box.fill("Tu te souviens de mes études ?");
  await box.press("Enter");
  const last = page.locator(".msg.assistant").last();
  await last.getByText("BUT GMP à Limoges").waitFor();
  await last.locator(".tag", { hasText: "llama-test:8b" }).waitFor();
  await last.getByText("1 souvenir utilisé").waitFor();
  assert.equal(await page.locator(".md script").count(), 0, "une balise script a été injectée");
  await last.getByText("<script>alert('xss')</script>").waitFor();
  await last.getByRole("button", { name: "Détail" }).click();
  await last.getByText("Souvenirs transmis au modèle").waitFor();
  await shot("2-conversation");
  step("autre modèle, nouvelle conversation : la mémoire suit ; le HTML du modèle n'est pas exécuté");

  // --- réflexion approfondie
  await page.locator(".composer select").selectOption("");
  await page.getByRole("button", { name: "Réflexion approfondie" }).click();
  await box.fill("Calcule le couple pour F = 2000 N et r = 60 mm");
  await box.press("Enter");
  const deep = page.locator(".msg.assistant").last();
  await deep.getByText("Corrigé après vérification").waitFor();
  await deep.getByText("120 N·m").waitFor();
  await deep.getByRole("button", { name: "Détail" }).click();
  await deep.getByText("Le rayon doit être converti").waitFor();
  await shot("3-reflexion-approfondie");
  await page.getByRole("button", { name: "Réflexion approfondie" }).click();
  step("réflexion approfondie : plan, vérification, correction");

  // --- la conversation rechargée est identique ; renommer ; recherche
  await page.locator(".conv-main").first().click();
  await page.locator(".msg.assistant").last().getByText("Corrigé après vérification").waitFor();
  await page.locator(".conv").first().hover();
  await page.locator(".conv").first().getByRole("button", { name: "Renommer" }).click();
  await page.locator(".conv-title", { hasText: "Titre renommé" }).waitFor();
  await page.getByLabel("Chercher dans les conversations").fill("étudiant Limoges");
  await page.locator(".conv.hit").first().waitFor();
  await page.getByLabel("Chercher dans les conversations").fill("");
  step("historique rechargé, renommage, recherche dans les conversations");

  // --- mémoire : ajouter, modifier, chercher, supprimer
  await nav("Mémoire").click();
  await page.getByRole("button", { name: "Liste", exact: true }).click();
  await page.locator(".mem", { hasText: "BUT GMP" }).waitFor();
  await page.getByRole("button", { name: "Ajouter" }).click();
  await page.getByLabel("Contenu du souvenir").fill("L'utilisateur joue au badminton en club le mardi");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await page.locator(".mem", { hasText: "badminton" }).waitFor();
  await page.locator(".mem", { hasText: "badminton" }).getByRole("button", { name: "Modifier" }).click();
  await page.getByLabel("Contenu du souvenir").fill("L'utilisateur joue au badminton en club le jeudi");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await page.locator(".mem", { hasText: "jeudi" }).waitFor();
  await page.getByLabel("Chercher un souvenir").fill("badminton");
  await page.waitForFunction(() => document.querySelectorAll(".mem").length === 1);
  await shot("4-memoire");
  await page.getByLabel("Chercher un souvenir").fill("");
  await page.waitForFunction(() => document.querySelectorAll(".mem").length === 2);
  await page.locator(".mem", { hasText: "jeudi" }).getByRole("button", { name: "Supprimer" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".mem").length === 1);
  step("mémoire : ajout, modification, recherche, suppression");

  // --- modèles : routage
  await nav("Modèles").click();
  await page.getByText("2 modèles").waitFor();
  await page.getByLabel("Programmation").selectOption("ollama:llama-test:8b");
  await page.waitForTimeout(300);
  await shot("5-modeles");
  await nav("Chat").click();
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await box.fill("Écris un script Python qui dit bonjour");
  await box.press("Enter");
  const routed = page.locator(".msg.assistant").last();
  await routed.locator(".tag", { hasText: "llama-test:8b" }).waitFor();
  await routed.locator(".tag", { hasText: "Programmation" }).waitFor();
  step("routage automatique : une demande de code part vers le modèle associé");

  // --- réglages et sauvegarde ; tableau de bord
  await nav("Paramètres").click();
  await page.getByLabel("Nom de l'assistant").fill("Friday");
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await page.waitForFunction(() => document.title === "Friday");
  await page.getByRole("button", { name: "Sauvegarder maintenant" }).click();
  const backups = page.locator('.card:has(h3:text-is("Sauvegardes"))');
  await backups.locator(".table tr").first().waitFor();
  await page.getByLabel("Nom de l'assistant").fill("Autre");
  await page.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await page.waitForFunction(() => document.title === "Autre");
  await backups.locator(".table tr").first().getByRole("button", { name: "Restaurer" }).click();
  await page.waitForFunction(() => document.title === "Friday");
  step("réglages enregistrés ; sauvegarde puis restauration");
  await nav("Tableau de bord").click();
  await page.locator(".tile", { hasText: "Conversations" }).getByText("3").waitFor();
  await shot("6-tableau-de-bord");
  step("tableau de bord : chiffres réels");

  // --- base de connaissances : import, classement, graphe, réponse sourcée, enrichissement
  const ACIER = "L'acier est un alliage de fer et de carbone. Sa résistance à la traction se mesure par un essai de traction normalisé. Le module de Young de l'acier vaut environ 210 GPa.";
  await nav("Connaissances").click();
  await page.getByText("La bibliothèque est vide").waitFor();
  await page.getByRole("tab", { name: "Importer" }).click();
  await page.locator('.drop input[type="file"]').setInputFiles({ name: "cours-rdm.txt", mimeType: "text/plain", buffer: Buffer.from(ACIER) });
  await page.locator(".jobs tr", { hasText: "cours-rdm.txt" }).locator(".pill.ok").waitFor();
  await page.locator(".jobs tr", { hasText: "cours-rdm.txt" }).getByText("classé dans « Sciences fondamentales › Matériaux et métallurgie »").waitFor();
  await page.locator('.drop input[type="file"]').setInputFiles({ name: "copie.txt", mimeType: "text/plain", buffer: Buffer.from(ACIER) });
  await page.locator(".jobs tr", { hasText: "copie.txt" }).getByText("doublon", { exact: true }).waitFor();
  await page.getByLabel("Chercher un article Wikipédia").fill("soudage");
  await page.getByRole("button", { name: "Chercher", exact: true }).click();
  await page.locator(".hit-row", { hasText: "Soudage" }).getByRole("button", { name: "Importer" }).click();
  await page.locator(".jobs tr", { hasText: "Soudage" }).locator(".pill.ok").waitFor();
  await shot("7-connaissances-import");
  step("connaissances : import de fichier classé automatiquement, doublon refusé, article Wikipédia importé");

  await page.getByRole("tab", { name: /Bibliothèque/ }).click();
  await page.locator(".mem", { hasText: "cours-rdm" }).locator(".cat", { hasText: "Matériaux et métallurgie" }).waitFor();
  await page.locator(".doc-title", { hasText: "Soudage" }).click();
  await page.locator(".passage", { hasText: "# Procédés" }).waitFor();
  assert.match(await page.getByLabel("Licence / droits").inputValue(), /CC BY-SA/);
  await page.getByRole("tab", { name: "Graphe" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".graph-card .node").length === 4);
  await page.locator(".node", { hasText: "acier" }).first().click();
  await shot("8-graphe");
  step("fiche document avec source et licence ; graphe extrait du document");

  await nav("Chat").click();
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await box.fill("Quel est le module de Young de l'acier ?");
  await box.press("Enter");
  const rag = page.locator(".msg.assistant").last();
  await rag.getByText("210 GPa").waitFor();
  await rag.locator(".tag", { hasText: "1 source citée" }).waitFor();
  await rag.locator(".source", { hasText: "cours-rdm" }).waitFor();
  await shot("9-reponse-sourcee");
  await rag.locator(".source", { hasText: "cours-rdm" }).click();
  await page.locator(".passage", { hasText: "210 GPa" }).waitFor();
  step("réponse appuyée sur la base, source citée et cliquable jusqu'au passage");

  await nav("Chat").click();
  await box.fill("Comment calculer le flambage d'une poutre comprimée ?");
  await box.press("Enter");
  await page.locator(".msg.assistant").last().locator(".tag", { hasText: "Sans source documentaire" }).waitFor();
  const offer = page.locator(".msg.assistant").last().locator(".research-offer");
  await offer.getByRole("button", { name: "Lancer une recherche" }).click();
  await offer.getByText(/Recherche n° 1 lancée/).waitFor();
  step("la base ne sait pas : Jarvis propose une recherche, qui ne part qu'au clic");
  await nav("Connaissances").click();
  await page.getByRole("tab", { name: /À traiter/ }).click();
  await page.locator(".hit-row", { hasText: "flambage d'une poutre" }).first().waitFor();
  await page.getByRole("button", { name: "Chercher de quoi combler les manques" }).click();
  const prop = page.locator(".hit-row", { hasText: "question restée sans source" });
  await prop.getByText("Flambage", { exact: true }).waitFor();
  await shot("10-a-traiter");
  await prop.getByRole("button", { name: "Accepter" }).click();
  await page.locator(".tabs button", { hasText: "Bibliothèque" }).locator("i", { hasText: "3" }).waitFor();
  step("question sans source repérée, proposition Wikipédia, import seulement après accord");

  await page.getByLabel("Recherche globale").fill("module Young");
  await page.locator(".gresults button", { hasText: "cours-rdm" }).click();
  await page.locator(".passage", { hasText: "210 GPa" }).waitFor();
  await page.getByRole("tab", { name: "Journal" }).click();
  await page.locator(".table tr", { hasText: "import" }).first().waitFor();
  step("recherche globale et journal des modifications");

  // --- sphère 3D : données réelles, sélection, exploration, synchronisation, suppression
  const call = (method, path, body) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, headers: { "Content-Type": "application/json", "X-Jarvis-Token": document.querySelector('meta[name="jarvis-token"]').content }, body: b ? JSON.stringify(b) : undefined });
    return r.json();
  }, [method, path, body]);
  const real = await call("GET", "/api/sphere");
  await nav("Mémoire").click();
  await page.waitForFunction((n) => window.__jarvisSphere && window.__jarvisSphere.count() === n && window.__jarvisSphere.frames() > 40, real.nodes.length);
  assert.equal(real.stats.documents, 3);
  await page.locator(".sphere-stats", { hasText: "3 documents" }).waitFor();
  await page.getByRole("button", { name: "Rotation automatique" }).click();
  await page.waitForTimeout(1600);   // fin de l'animation d'entrée
  const box3d = await page.locator(".sphere-gl").boundingBox();
  const px = await sharp(await page.screenshot({ clip: box3d })).stats();
  assert.ok(px.channels[2].max > 200 && px.channels[2].mean > 6, "la sphère ne dessine rien");
  await shot("11-sphere");
  let at = await page.evaluate(() => window.__jarvisSphere.screenOf("f:sciences"));
  await page.mouse.click(at.x, at.y);
  await page.locator(".sphere-detail h3", { hasText: "Sciences" }).waitFor();
  await page.locator(".sphere-detail").getByRole("button", { name: "cours-rdm" }).waitFor().catch(() => {});
  step("sphère 3D : rendu WebGL, nœuds issus de la base, clic sur une famille");

  await page.getByLabel("Rechercher dans la sphère").fill("cours-rdm");
  await page.locator(".sphere-hits button", { hasText: "cours-rdm" }).click();
  const det = page.locator(".sphere-detail");
  await det.locator("h3", { hasText: "cours-rdm" }).waitFor();
  await det.getByText("Sciences fondamentales › Matériaux et métallurgie").waitFor();
  await det.getByText("cours-rdm.txt").waitFor();
  await det.getByText("a pour propriété").first().waitFor();
  await page.waitForTimeout(1000);
  at = await page.evaluate(() => window.__jarvisSphere.screenOf("d:1"));
  const mid = { x: box3d.x + box3d.width / 2, y: box3d.y + box3d.height / 2 };
  assert.ok(Math.hypot(at.x - mid.x, at.y - mid.y) < 60, "le zoom n'a pas centré le nœud sélectionné");
  await shot("12-sphere-detail");
  await det.getByRole("button", { name: /Explorer plus loin/ }).click();
  await det.getByText("2 pas").waitFor();
  await det.getByRole("button", { name: "acier", exact: true }).first().click();
  await det.locator("h3", { hasText: "acier" }).waitFor();
  await det.getByText("Concept", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "Relations" }).click();
  await page.getByRole("tab", { name: "Mémoire perso." }).click();
  step("recherche, zoom sur le nœud, fiche (catégorie, source, relations), exploration de proche en proche");

  await call("POST", "/api/memories", { content: "L'utilisateur prépare un treuil Huchez pour son projet de construction", category: "projets", importance: 4 });
  await page.locator(".sphere-stats", { hasText: "2 souvenirs" }).waitFor({ timeout: 9000 });
  await page.getByRole("tab", { name: "Projets" }).click();
  await page.getByLabel("Rechercher dans la sphère").fill("treuil");
  await page.locator(".sphere-hits button", { hasText: "treuil" }).click();
  await det.getByText("Projets", { exact: true }).first().waitFor();
  await det.getByRole("button", { name: "Supprimer" }).click();
  await page.locator(".sphere-stats", { hasText: "1 souvenirs" }).waitFor();
  assert.equal((await call("GET", "/api/memories")).length, 1);
  step("synchronisation automatique après un ajout ; suppression confirmée depuis la sphère");

  // --- centre de connaissances : tableau de bord, export
  await nav("Connaissances").click();
  await page.getByRole("tab", { name: "Tableau de bord" }).click();
  await page.locator(".tile", { hasText: "Documents indexés" }).getByText("3", { exact: true }).waitFor();
  await page.locator(".tile", { hasText: "Souvenirs personnels" }).getByText("1", { exact: true }).waitFor();
  await page.locator(".modrow", { hasText: "Bibliothèque technique" }).waitFor();
  await page.locator(".card", { hasText: "Dernières recherches du chercheur" }).getByText("flambage").waitFor();
  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exporter la base" }).click();
  assert.match((await dl).suggestedFilename(), /^jarvis-connaissances-.*\.zip$/);
  await page.locator(".toast", { hasText: "Export de 3 document" }).waitFor();
  await shot("13-centre-de-connaissances");
  step("centre de connaissances : chiffres réels, modules, dernières recherches, export téléchargé");

  // --- bibliothèque technique : formules, tableaux, données, versions
  const POUTRE = "Cours de flexion des poutres, première édition. La contrainte normale s'écrit σ = F / S avec F l'effort.\n\n| Matériau | E (GPa) |\n| acier | 210 |\n| aluminium | 69 |\n\nLa flèche d'une poutre encastrée vaut f = F*L^3/(3*E*I). Limite d'élasticité du S235 : 235 MPa.";
  await page.getByRole("tab", { name: "Importer" }).click();
  await page.locator('.drop input[type="file"]').setInputFiles({ name: "flexion v1.txt", mimeType: "text/plain", buffer: Buffer.from(POUTRE) });
  await page.locator(".jobs tr", { hasText: "flexion v1.txt" }).locator(".pill.ok").waitFor();
  await page.locator(".jobs tr", { hasText: "flexion v1.txt" }).getByText(/relevé : 2 formule\(s\), 1 tableau\(s\), 1 donnée\(s\)/).waitFor();
  await page.locator('.drop input[type="file"]').setInputFiles({ name: "flexion v2.txt", mimeType: "text/plain",
    buffer: Buffer.from(POUTRE.replace("première édition", "deuxième édition corrigée") + " Ce chapitre ajoute la torsion des arbres pleins.") });
  await page.locator(".jobs tr", { hasText: "flexion v2.txt" }).getByText(/semble remplacer le document/).waitFor();
  await page.getByRole("tab", { name: /Bibliothèque/ }).click();
  await page.locator(".mem", { hasText: "flexion v1" }).locator(".tag", { hasText: "version plus récente disponible" }).waitFor();
  await page.locator(".doc-title", { hasText: "flexion v2" }).click();
  await page.getByRole("heading", { name: /Formules, tableaux et données relevés/ }).waitFor();
  await page.locator(".chip.mono", { hasText: "f = F*L^3/(3*E*I)" }).waitFor();
  await page.locator(".table.data td", { hasText: "aluminium" }).waitFor();
  await page.locator(".chip", { hasText: "235 MPa" }).waitFor();
  await shot("14-formules-tableaux");
  await page.getByRole("tab", { name: /À traiter/ }).click();
  const olds = page.locator(".card", { hasText: "Anciennes versions (1)" });
  await olds.getByText("remplacé par « flexion v2 »").waitFor();
  await olds.getByRole("button", { name: "Archiver l'ancienne" }).click();
  await page.locator(".toast", { hasText: "Ancienne version archivée" }).waitFor();
  await page.locator(".card", { hasText: "Anciennes versions (0)" }).waitFor();
  step("bibliothèque : formules, tableau et donnée relevés ; ancienne version signalée puis archivée sur décision");

  // --- recherche croisée par module, formules
  await page.getByRole("tab", { name: "Recherche", exact: true }).click();
  await page.getByLabel("Chercher dans la base").fill("module de Young");
  await page.locator(".mem", { hasText: "cours-rdm" }).locator(".cat", { hasText: "Bibliothèque technique" }).waitFor();
  await page.getByLabel("Type de résultat").selectOption("formule");
  await page.getByLabel("Chercher dans la base").fill("");
  await page.locator(".excerpt.mono", { hasText: "σ = F / S" }).waitFor();
  assert.equal(await page.locator(".excerpt.mono", { hasText: "σ = F / S" }).count(), 1, "la version archivée ne doit plus répondre");
  step("recherche dans tout le cerveau, résultats étiquetés par module ; recherche de formules");

  // --- dossier de la bibliothèque
  const dataDir = (await call("GET", "/api/state")).data_dir;
  await page.getByRole("tab", { name: /Indexation/ }).click();
  await page.getByLabel("Dossier de la bibliothèque").fill(`${dataDir}/bibli`);
  await page.getByRole("button", { name: "Analyser maintenant" }).click();
  await page.locator(".toast", { hasText: "2 fichier(s) lisible(s) : 2 nouveau(x)" }).waitFor();
  await page.locator(".jobs tr", { hasText: "fraisage.md" }).locator(".pill.ok").waitFor();
  await page.locator(".jobs tr", { hasText: "atelier.txt" }).locator(".pill.ok").waitFor();
  await page.getByText("2 fichier(s) suivis · 2 indexé(s)").waitFor();
  await page.getByRole("button", { name: "Analyser maintenant" }).click();
  await page.locator(".toast", { hasText: "2 fichier(s) lisible(s) : 0 nouveau(x), 0 modifié(s)" }).waitFor();
  await shot("15-indexation");
  step("dossier du PC indexé en entier ; une seconde analyse ne refait rien");

  // --- chercheur autonome
  await page.getByRole("tab", { name: /Chercheur/ }).click();
  await page.getByLabel("Question à rechercher").fill("Quel est le module de Young de l'acier ?");
  await page.getByRole("button", { name: "Chercher", exact: true }).click();
  await page.locator(".card .pill.ok", { hasText: "terminé" }).waitFor({ timeout: 20000 });
  await page.locator(".steps", { hasText: "1 citation(s) écartée(s) car introuvable(s) dans la source" }).waitFor();
  assert.equal(await page.locator(".finding").count(), 2);
  const ok = page.locator(".finding", { hasText: "vaut environ 210 GPa" });
  await ok.locator(".tag.ok", { hasText: "vérifié" }).waitFor();
  await ok.getByText("hal.science").waitFor();
  await page.locator(".finding", { hasText: "diminue quand la température" }).locator(".tag.warn", { hasText: "à confirmer" }).waitFor();
  assert.equal(await page.getByText("inventé en 1850").count(), 0, "une affirmation sans citation retrouvée a été gardée");
  assert.equal((await call("GET", "/api/kb/documents?module=recherche")).length, 0, "une connaissance est entrée sans validation");
  await shot("16-chercheur");
  await ok.getByRole("button", { name: "Valider et ajouter à la base" }).click();
  await page.locator(".toast", { hasText: "Connaissance ajoutée à la base" }).waitFor();
  await ok.locator(".tag.ok", { hasText: "intégré" }).waitFor();
  assert.equal((await call("GET", "/api/kb/documents?module=recherche")).length, 1);
  await page.getByRole("button", { name: "← Toutes les recherches" }).click();
  await page.locator(".card", { hasText: "À valider (1)" }).waitFor();
  await page.locator(".tabs button", { hasText: "Chercheur" }).locator("b", { hasText: "1" }).waitFor();
  step("chercheur : sources interrogées, citations contrôlées, rien n'entre sans validation, puis intégration");

  await page.getByRole("tab", { name: "Sources" }).click();
  const wiki = page.locator(".table tr", { hasText: "wikipedia.org" });
  await wiki.waitFor();
  await wiki.getByRole("button", { name: "Bloquer" }).click();
  await wiki.locator(".tag.warn", { hasText: "Bloqué" }).waitFor();
  await wiki.getByRole("button", { name: "Débloquer" }).click();
  await wiki.getByRole("button", { name: "Bloquer" }).waitFor();
  step("suivi des sources : blocage et déblocage d'un site");

  // --- sphère : modules, apparition progressive
  const full = await call("GET", "/api/sphere");
  await nav("Mémoire").click();
  await page.waitForFunction((n) => window.__jarvisSphere && window.__jarvisSphere.count() === n && window.__jarvisSphere.frames() > 40, full.nodes.length);
  assert.ok(full.nodes.some((n) => n.k === "research" && n.m === "recherche"), "la recherche validée n'est pas dans la sphère");
  const modBtn = page.locator(".sphere-left .fam", { hasText: "Recherches du chercheur" });
  await modBtn.locator("b", { hasText: "2" }).waitFor();
  const visBefore = await page.evaluate(() => window.__jarvisSphere.visible());
  await modBtn.click();
  await page.waitForFunction((b) => window.__jarvisSphere.visible() < b && window.__jarvisSphere.visible() >= 2, visBefore);
  await page.getByRole("button", { name: "Couleurs par module" }).click();
  await page.waitForTimeout(700);
  await shot("17-sphere-modules");
  await page.getByRole("button", { name: "Tous les modules" }).click();
  await page.waitForFunction((b) => window.__jarvisSphere.visible() === b, visBefore);
  assert.equal(await page.evaluate(() => window.__jarvisSphere.spawned()), 0);
  await call("POST", "/api/memories", { content: "L'utilisateur révise le module de Young de l'acier pour son partiel de RDM", category: "études", importance: 3 });
  await page.waitForFunction(() => window.__jarvisSphere.spawned() >= 1, null, { timeout: 9000 });
  await page.waitForTimeout(900);
  await shot("18-sphere-apparition");
  step("sphère : filtre par module, couleurs par module, nouvelle connaissance qui apparaît progressivement");

  // --- réponse : modules consultés
  await nav("Chat").click();
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await box.fill("Quel est le module de Young de l'acier ?");
  await box.press("Enter");
  const ans = page.locator(".msg.assistant").last();
  await ans.locator(".md").getByText("210 GPa").waitFor();
  await ans.getByRole("button", { name: "Détail" }).click();
  await ans.locator(".mods li", { hasText: "Recherches du chercheur" }).getByText(/1 élément retenu/).waitFor();
  await ans.locator(".mods li", { hasText: "Bibliothèque technique" }).getByText(/éléments? retenus?/).waitFor();
  await ans.getByText("domaine identifié : Résistance des matériaux").waitFor();
  await shot("19-modules-consultes");
  await box.fill("Merci !");
  await box.press("Enter");
  const small = page.locator(".msg.assistant").last();
  await small.locator(".md").waitFor();
  await small.getByRole("button", { name: "Détail" }).click();
  await small.locator(".mods li.off", { hasText: "Bibliothèque technique" }).waitFor();
  step("réponse : modules consultés affichés ; un simple « merci » ne déclenche aucune recherche documentaire");

  // --- cerveau central : tâche complexe découpée en étapes, exécutée, vérifiée, tracée
  await box.fill("Prépare-moi un plan de révision complet pour mon partiel de RDM :\n- liste les chapitres\n- propose ensuite un planning sur deux semaines\n- puis donne trois exercices types");
  await box.press("Enter");
  const big = page.locator(".msg.assistant").last();
  await big.locator(".md").getByText("plan de révision").waitFor();
  await big.locator(".tag", { hasText: "En étapes · 3/3" }).waitFor();
  await big.locator(".tag", { hasText: "Vérifié" }).waitFor();
  await big.getByRole("button", { name: "Détail" }).click();
  await big.getByText("Organiser les révisions du partiel de RDM").waitFor();
  await big.locator(".steps li", { hasText: "Planning sur deux semaines" }).getByText("terminée").waitFor();
  await big.locator(".steps li", { hasText: "Exercices types" }).getByText("Flambage d'Euler").waitFor();
  await shot("21-taches-en-etapes");
  await box.fill("Ouvre la calculatrice");
  await box.press("Enter");
  const tool = page.locator(".msg.assistant").last();
  await tool.getByRole("button", { name: "Lancer Calculatrice" }).click();
  await tool.getByText("Calculatrice a été lancé à votre demande.").waitFor();
  step("cerveau central : tâche complexe en 3 étapes exécutées et vérifiées ; outil proposé, lancé seulement sur accord");

  // --- mémoire intelligente : correction proposée, validée, historique gardé
  await box.fill("Au fait, je suis passé en deuxième année");
  await box.press("Enter");
  await page.getByText(/mise à jour de souvenir à valider/).waitFor();
  await nav("Mémoire").click();
  await page.getByRole("button", { name: "Liste", exact: true }).click();
  const mprop = page.locator(".proposal");
  await mprop.locator(".old", { hasText: "étudiant en BUT GMP à Limoges" }).waitFor();
  await mprop.locator(".new", { hasText: "deuxième année" }).waitFor();
  await shot("22-memoire-a-valider");
  await mprop.getByRole("button", { name: "Accepter" }).click();
  await mprop.waitFor({ state: "detached" });
  const card = page.locator(".mem", { hasText: "deuxième année de BUT GMP" });
  await card.getByRole("button", { name: "Historique" }).click();
  await card.locator(".history li", { hasText: "Mise à jour validée" }).getByText("L'utilisateur est étudiant en BUT GMP à Limoges").waitFor();
  await card.locator(".nat", { hasText: "Personnelle" }).waitFor();
  await page.locator(".filters select[aria-label='Nature']").selectOption("technique");
  await page.getByText("Aucun souvenir ne correspond.").waitFor();
  await page.locator(".filters select[aria-label='Nature']").selectOption("");
  await page.getByRole("button", { name: "Sphère 3D" }).click();
  await page.locator(".sphere-gl").waitFor();
  await nav("Tableau de bord").click();
  await page.getByText("Cerveau central").first().waitFor();
  await page.locator(".bar", { hasText: "Décomposée en étapes" }).waitFor();
  await page.getByText("Habitudes observées").waitFor();
  await shot("23-tableau-cerveau");
  step("mémoire : mise à jour proposée puis acceptée, ancienne version dans l'historique ; tableau de bord du cerveau");
  await nav("Chat").click();

  // --- mode vocal (reconnaissance et synthèse simulées)
  await nav("Chat").click();
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await page.getByRole("button", { name: /Parler à/ }).click();
  await page.locator(".voicebar.v-listening").waitFor();
  await page.evaluate(() => window.__say("quel est le module de Young", false));
  await page.locator(".voice-heard", { hasText: "quel est le module de Young" }).waitFor();
  await page.evaluate(() => window.__say("Quel est le module de Young de l'acier ?"));
  await page.locator(".msg.user", { hasText: "module de Young de l'acier" }).waitFor();
  await page.locator(".msg.assistant").last().locator(".md").getByText("210 GPa").waitFor();
  await page.waitForFunction(() => window.__voice.spoken.some((t) => t.includes("210 GPa")));
  const spoken = await page.evaluate(() => window.__voice.spoken.join(" | "));
  assert.ok(!/\*|\[S1\]/.test(spoken), "le texte lu contient des symboles : " + spoken);
  await page.locator(".voicebar.v-listening").waitFor();   // conversation continue : il réécoute sans clic
  step("voix : phrase détectée à la fin du silence, réponse lue sans symboles, réécoute automatique");

  await page.evaluate(() => { window.__voice.slow = true; window.__voice.cancelled = 0; });
  await page.evaluate(() => window.__say("Rappelle-moi le module de Young de l'acier"));
  await page.locator(".voicebar.v-speaking").waitFor();
  await page.evaluate(() => window.__say("D'après votre cours le module de Young de l'acier vaut environ", false));   // écho de sa propre voix
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => window.__voice.cancelled), 0, "l'écho de sa propre voix l'a interrompu");
  await page.evaluate(() => window.__say("stop"));
  await page.locator(".voicebar.v-listening").waitFor();
  assert.ok(await page.evaluate(() => window.__voice.cancelled) > 0);
  await page.evaluate(() => { window.__voice.slow = false; });
  step("voix : sa propre voix ne l'interrompt pas, « stop » lui coupe la parole");

  await page.waitForTimeout(400);
  await page.evaluate(() => window.__say("lance la calculatrice"));
  await page.locator(".toast", { hasText: "Calculatrice lancé" }).waitFor();
  assert.match((await call("GET", "/api/actions"))[0].detail, /Calculatrice lancé/);
  const launches = (await call("GET", "/api/actions")).filter((a) => a.kind === "application").length;
  await page.locator(".voicebar.v-listening").waitFor();
  await page.evaluate(() => window.__say("lance photoshop"));   // pas dans la liste autorisée : rien n'est lancé
  await page.locator(".msg.user", { hasText: "lance photoshop" }).waitFor();
  assert.equal((await call("GET", "/api/actions")).filter((a) => a.kind === "application").length, launches, "une application hors liste a été lancée");
  await page.locator(".voicebar.v-listening").waitFor();
  await page.evaluate(() => window.__say("ouvre la mémoire"));
  await page.locator(".sphere-gl").waitFor();
  step("voix : lancement d'une application autorisée, refus hors liste, navigation");

  await nav("Chat").click();
  await page.getByRole("button", { name: "Arrêter le mode vocal" }).click();
  await page.locator(".voicebar").waitFor({ state: "detached" });
  await nav("Paramètres").click();
  await page.getByText("Mains libres : écouter en permanence le mot-clé").click();
  await nav("Chat").click();
  await page.locator(".voicebar.v-standby").waitFor();
  const before = await page.locator(".msg.user").count();
  await page.evaluate(() => window.__say("il fait beau aujourd'hui je crois"));
  await page.waitForTimeout(1900);
  assert.equal(await page.locator(".msg.user").count(), before, "une phrase sans mot-clé a été envoyée");
  await page.locator(".voicebar.v-standby").waitFor();
  await page.evaluate(() => window.__say("Jarvis, combien font deux plus deux"));
  await page.locator(".msg.user", { hasText: "combien font deux plus deux" }).waitFor();
  assert.ok(!(await page.locator(".msg.user").last().innerText()).toLowerCase().includes("jarvis"));
  await page.locator(".voicebar.v-listening").waitFor();
  await shot("20-vocal");
  await page.evaluate(() => window.__voice.cur.onerror({ error: "not-allowed" }));
  await page.locator(".voicebar.v-error", { hasText: "Accès au micro refusé" }).waitFor();
  step("mains libres : rien sans le mot-clé, demande prise après le mot-clé ; micro refusé signalé clairement");

  // ===================== 0.7 — espace de travail : artefacts, images, navigateur, projets, missions
  await page.evaluate(() => window.__voice.cur && window.__voice.cur.onerror && null);
  await nav("Chat").click();
  await page.getByRole("button", { name: "Nouvelle conversation" }).click();
  await box.fill("Crée-moi un outil compteur pour l'atelier");
  await box.press("Enter");
  const mb = page.locator(".msg.assistant").last();
  await mb.locator(".mission-block").waitFor();
  await mb.locator(".agent", { hasText: "Développeur" }).waitFor();
  await page.locator(".wpanel iframe").waitFor();
  const sandbox = () => page.frames().find((f) => f.url().includes("/sandbox/a/"));
  await page.waitForFunction(() => document.querySelector(".wpanel iframe"));
  let fr = null;
  for (let i = 0; i < 50 && !(fr = sandbox()); i++) await page.waitForTimeout(100);
  await fr.waitForSelector("#plus");
  await fr.click("#plus"); await fr.click("#plus");
  assert.equal(await fr.textContent("#v"), "2", "l'artefact ne s'exécute pas réellement");
  const isolated = await fr.evaluate(async () => {
    let parentRead = "lu"; try { void window.parent.document.body; } catch { parentRead = "bloqué"; }
    let net = "envoyé"; try { await fetch("/api/state"); } catch { net = "bloqué"; }
    return [parentRead, net, String(window.origin)];
  });
  assert.deepEqual(isolated, ["bloqué", "bloqué", "null"], "l'artefact n'est pas isolé");
  await mb.locator(".acard", { hasText: "Compteur d'atelier" }).waitFor();
  await mb.getByText("Contrôle de l'artefact").waitFor();
  await shot("21-artefact");
  step("artefact créé par une mission (développeur puis vérificateur), aperçu réel et isolé dans le panneau de droite");

  await page.locator(".composer-ctx", { hasText: "Compteur d'atelier" }).waitFor();
  await box.fill("Change le titre en rouge");
  await box.press("Enter");
  await page.locator(".wpanel select[aria-label='Version'] option", { hasText: "v2 (actuelle)" }).waitFor({ state: "attached" });
  for (let i = 0; i < 50; i++) { fr = sandbox(); if (fr && (await fr.textContent("#titre").catch(() => "")) === "Compteur rouge") break; await page.waitForTimeout(100); }
  assert.equal(await sandbox().textContent("#titre"), "Compteur rouge");
  step("modification demandée dans la conversation : nouvelle version, aperçu actualisé automatiquement");

  await page.locator(".wpanel").getByRole("button", { name: "Sélectionner un élément" }).click();
  await sandbox().click("#plus");
  await page.locator(".sel-chip code", { hasText: "#plus" }).waitFor();
  await page.locator(".wpanel input[aria-label='Modification demandée']").fill("Rends ce bouton plus grand");
  await page.locator(".wpanel").getByRole("button", { name: "Modifier" }).last().click();
  await page.locator(".wpanel select[aria-label='Version'] option", { hasText: "v3 (actuelle)" }).waitFor({ state: "attached" });
  const sentSel = await call("GET", "/api/missions?limit=1");
  assert.equal(sentSel[0].plan.steps[0].tool, "artifact.edit");
  await page.locator(".wpanel").getByRole("button", { name: "Code" }).click();
  assert.ok((await page.locator(".aview-code textarea").inputValue()).includes("font-size:30px"));
  await page.locator(".wpanel select[aria-label='Version']").selectOption("1");
  await page.locator(".notice", { hasText: "Vous regardez la version 1" }).waitFor();
  await page.getByRole("button", { name: "Revenir à cette version" }).click();
  await page.locator(".wpanel select[aria-label='Version'] option", { hasText: "v4 (actuelle)" }).waitFor({ state: "attached" });
  step("sélection d'un élément de l'aperçu puis modification ciblée ; code visible ; retour à une version précédente");

  await page.getByRole("radio", { name: "Image" }).click();
  await box.fill("une maison moderne au bord d'un lac");
  await box.press("Enter");
  const ib = page.locator(".msg.assistant").last();
  await ib.locator(".itile img").waitFor();
  await ib.locator(".agent", { hasText: "Concepteur" }).waitFor();
  await ib.getByRole("button", { name: "Enregistrer" }).click();
  await page.locator(".toast", { hasText: "Image enregistrée" }).waitFor();
  await shot("22-image-chat");
  await box.fill("Jarvis, génère-moi une voiture de sport en 3D.");
  await box.press("Enter");
  await page.locator(".msg.assistant").last().locator(".itile img").waitFor();
  step("images : outil choisi sous la zone de saisie, puis demande ambiguë reconnue par le modèle ; image enregistrée");

  await nav("Images").click();
  await page.getByRole("button", { name: /Galerie/ }).click();
  assert.equal(await page.locator(".studio-main .igrid .itile").count(), 1);
  await page.getByRole("button", { name: "Résultats et récentes" }).click();
  await page.getByLabel("Description").fill("un logo pour mon projet de treuil");
  await page.locator(".chip", { hasText: "Logo" }).click();
  await page.locator(".seg.five button", { hasText: "Paysage" }).click();
  await page.getByRole("button", { name: "Générer" }).click();
  await page.locator("h4", { hasText: "Dernière génération" }).waitFor();
  await shot("23-images");
  await page.locator(".igrid.big .itile").first().getByRole("button", { name: "Modifier" }).click();
  await page.getByLabel("Modification", { exact: true }).fill("ajoute un engrenage au centre");
  await page.getByRole("button", { name: "Appliquer la modification" }).click();
  await page.locator(".toast", { hasText: "générée" }).last().waitFor();
  step("génération d'images : format, style, modification d'une image générée, galerie");

  await nav("Navigateur").click();
  await page.getByLabel("Adresse ou recherche").fill(`${webUrl}/treuil`);
  await page.getByLabel("Adresse ou recherche").press("Enter");
  await page.locator(".reader h1", { hasText: "Treuils électriques" }).waitFor();
  assert.equal(await page.locator(".reader script").count(), 0);
  await page.locator(".reader td", { hasText: "4 m/min" }).waitFor();
  await page.locator(".reader a", { hasText: "les règles de levage" }).click();
  await page.locator(".reader h1", { hasText: "Règles de levage" }).waitFor();
  await page.getByRole("button", { name: "Précédente" }).click();
  await page.locator(".reader h1", { hasText: "Treuils électriques" }).waitFor();
  await page.getByRole("button", { name: "Ajouter aux favoris" }).click();
  await page.getByRole("button", { name: "Retirer des favoris" }).waitFor();
  await page.getByRole("button", { name: "Historique" }).click();
  await page.locator(".bside .bitem", { hasText: "Règles de levage" }).waitFor();
  await shot("24-navigateur");
  step("navigateur : page en mode lecture sans scripts, tableau, lien suivi, retour, favori, historique");

  await page.getByRole("button", { name: "Demander à Jarvis sur cette page" }).click();   // la boîte de dialogue est acceptée par le test
  await page.locator(".msg.user", { hasText: "Titre renommé" }).waitFor();
  const wb = page.locator(".msg.assistant").last();
  await wb.locator(".source.web", { hasText: "Treuils électriques" }).waitFor();
  await wb.getByText("500 kg").waitFor();
  await wb.locator(".agent", { hasText: "Chercheur" }).waitFor();
  step("question sur la page ouverte : le chercheur lit l'onglet, réponse avec la source web [W1]");

  await page.locator(".thread-head select[aria-label='Projet']").waitFor();
  await nav("Projets").click();
  await page.getByRole("textbox", { name: "Nom", exact: true }).fill("Treuil Huchez");
  await page.getByRole("textbox", { name: "Objectif", exact: true }).fill("Concevoir le treuil du projet S3");
  await page.getByRole("button", { name: "Créer" }).click();
  await page.getByRole("heading", { name: "Treuil Huchez" }).waitFor();
  await nav("Chat").click();
  await page.locator(".thread-head select[aria-label='Projet']").selectOption({ label: "Treuil Huchez" });
  await page.locator(".toast", { hasText: "rattachée au projet" }).waitFor();
  await nav("Projets").click();
  await page.locator(".pcard", { hasText: "Treuil Huchez" }).click();
  await page.getByRole("button", { name: /Conversations/ }).click();
  await page.locator(".plist .bitem").first().waitFor();
  await page.getByRole("button", { name: /Recherches et sources/ }).click();
  await page.locator(".bitem", { hasText: "Treuils électriques" }).waitFor();     // ce que la conversation a produit a rejoint le projet
  await page.getByRole("button", { name: /Artefacts/ }).last().click();
  await page.locator(".plist .bitem", { hasText: "Compteur d'atelier" }).waitFor();
  await page.getByRole("button", { name: /Recherches et sources/ }).click();
  await shot("25-projet");
  await nav("Artefacts").click();
  await page.locator(".aitem", { hasText: "Compteur d'atelier" }).click();
  await page.locator(".studio-main iframe").waitFor();
  await shot("26-artefacts");
  step("projets : création, conversation rattachée, sources retrouvées ; bibliothèque d'artefacts");

  await nav("Paramètres").click();
  await page.getByLabel("Fournisseur").selectOption("cloudflare");
  await page.getByRole("textbox", { name: "Identifiant de compte" }).fill("0123456789abcdef0123456789abcdef");
  await page.getByLabel(/Jeton d'API/).fill("jeton-cloudflare-de-test-0001");
  await page.getByRole("button", { name: "Enregistrer les accès Cloudflare" }).click();
  await page.locator(".toast", { hasText: "Cloudflare Workers AI enregistré" }).waitFor();
  const stc = await call("GET", "/api/studio");
  assert.equal(stc.images.provider, "cloudflare");
  assert.ok(stc.images.cf_token && !JSON.stringify(stc).includes("jeton-cloudflare-de-test"), "le jeton ne doit jamais revenir à l'interface");
  await page.locator(".notice", { hasText: "Cloudflare Workers AI (FLUX, gratuit) prêt" }).waitFor();
  await shot("27-cloudflare");
  step("générateur d'images gratuit : Cloudflare configuré dans les paramètres, jeton chiffré et jamais renvoyé");

  await page.getByLabel("Fournisseur").selectOption("comfyui");
  const cu = page.getByRole("textbox", { name: "Adresse de ComfyUI" });
  await cu.fill(comfyUrl);
  await cu.blur();
  await page.waitForFunction(async () => true);
  await page.getByRole("button", { name: "Tester et lister les modèles" }).click();
  await page.locator(".toast", { hasText: "2 modèle(s) d'images disponible(s)" }).waitFor();
  await page.getByLabel("Modèle (checkpoint)").selectOption("dreamshaperXL_lightningDPMSDE.safetensors");
  await nav("Images").click();
  await page.getByLabel("Description").fill("un treuil électrique d'atelier, rendu technique");
  await page.getByRole("button", { name: "Générer" }).click();
  await page.locator(".toast", { hasText: "dreamshaperXL_lightningDPMSDE" }).waitFor();
  await shot("28-comfyui");
  step("générateur local : ComfyUI sur le PC détecté, modèle choisi, image générée (gratuit, illimité)");

  assert.deepEqual(errors.filter((e) => !e.includes("401") && !/Refused to connect|connect-src 'none'/.test(e)), [], "erreurs dans la console");
  // (les seuls messages filtrés ci-dessus sont ceux du test d'isolement : l'artefact a tenté d'appeler l'API et a été bloqué)
  console.log("\nInterface : tous les parcours passent.");
} catch (e) {
  console.error("\nÉCHEC :", e.message, "\nconsole:", errors);
  console.error("dernière réponse :", await page.locator(".msg.assistant").last().innerText().catch(() => "?"));
  if (shots) await page.screenshot({ path: `${shots}/echec.png` });
  process.exitCode = 1;
} finally {
  await browser.close();
  py.kill();
}
