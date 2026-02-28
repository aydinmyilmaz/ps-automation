/*
  Photoshop Batch Name Export (PNG)
  - Opens nothing: works on currently active/open PSD
  - Finds text layer by name recursively (groups supported)
  - Reuses one opened document and resets with HistoryState per name
  - Exports PNG files into selected folder
*/

app.displayDialogs = DialogModes.NO;

// 1) Edit this layer name to match your PSD text layer.
var TARGET_LAYER_NAME = "NAME_TEXT";

// 2) Edit your names here.
var NAMES = [
  "KEREM",
  "MERT",
  "AYSE",
  "ZEYNEP",
  "ALI",
  "ELIF",
  "DENIZ",
  "ARDA",
  "SU",
  "MELIS"
];

if (app.documents.length === 0) {
  alert("Acik bir PSD yok. Once PSD dosyasini ac.");
  throw new Error("No open document");
}

var doc = app.activeDocument;

function sanitizeFilename(name) {
  var s = name.replace(/[^a-zA-Z0-9_-]/g, "_");
  s = s.replace(/_+/g, "_");
  s = s.replace(/^_+|_+$/g, "");
  if (s.length === 0) s = "untitled";
  return s;
}

function findLayerRecursive(container, targetName) {
  // Search direct art layers first
  for (var i = 0; i < container.artLayers.length; i++) {
    if (container.artLayers[i].name === targetName) return container.artLayers[i];
  }
  // Search inside groups
  for (var j = 0; j < container.layerSets.length; j++) {
    var found = findLayerRecursive(container.layerSets[j], targetName);
    if (found) return found;
  }
  return null;
}

function ensureUniqueFile(folder, baseName) {
  var candidate = new File(folder.fsName + "/" + baseName + ".png");
  if (!candidate.exists) return candidate;
  var idx = 2;
  while (true) {
    candidate = new File(folder.fsName + "/" + baseName + "_" + idx + ".png");
    if (!candidate.exists) return candidate;
    idx++;
  }
}

function exportPng24(documentRef, fileRef) {
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false; // PNG-24
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(fileRef, ExportType.SAVEFORWEB, opts);
}

var targetLayer = findLayerRecursive(doc, TARGET_LAYER_NAME);
if (!targetLayer) {
  alert("Text layer bulunamadi: " + TARGET_LAYER_NAME + "\nLayer adini kontrol et.");
  throw new Error("Layer not found: " + TARGET_LAYER_NAME);
}

if (targetLayer.kind !== LayerKind.TEXT) {
  alert("Bulunan layer text degil: " + TARGET_LAYER_NAME);
  throw new Error("Layer is not text");
}

var outFolder = Folder.selectDialog("PNG cikti klasorunu sec");
if (!outFolder) {
  alert("Islem iptal edildi (klasor secilmedi).");
  throw new Error("Output folder not selected");
}

var baseState = doc.activeHistoryState;
var exportedCount = 0;
var failedNames = [];

for (var n = 0; n < NAMES.length; n++) {
  var currentName = NAMES[n];
  try {
    doc.activeHistoryState = baseState;
    targetLayer.textItem.contents = currentName;

    var safe = sanitizeFilename(currentName);
    var outFile = ensureUniqueFile(outFolder, safe);
    exportPng24(doc, outFile);
    exportedCount++;
  } catch (e) {
    failedNames.push(currentName + " (" + e + ")");
  }
}

// Keep PSD unchanged in-memory for safety.
doc.activeHistoryState = baseState;

var msg = "Bitti.\nToplam: " + NAMES.length + "\nBasarili: " + exportedCount + "\nHatali: " + failedNames.length;
if (failedNames.length > 0) msg += "\n\nHatalar:\n- " + failedNames.join("\n- ");
alert(msg);
