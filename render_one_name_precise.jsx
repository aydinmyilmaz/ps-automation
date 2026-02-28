/*
  Render exactly one name with explicit style group control.
  Use this when the PSD has multiple style groups (metal / ALTIN / MAVI / PEMBE).
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = "/Users/aydin/Desktop/apps/ps-automation/data/Bootleg_STARTUP_2026.psd";
var OUTPUT_PATH = "/Users/aydin/Desktop/apps/ps-automation/output/KEREM_PRECISE.png";
var RENDER_NAME = "KEREM";

// Pick one: "metal", "ALTIN", "MAVI", "PEMBE"
var STYLE_GROUP_TO_USE = "metal";

// If true: only the chosen style group is visible (text-only style export).
// If false: keep the rest of design visible, but force only one style group active.
var TEXT_ONLY_MODE = false;

var SMART_LAYER_NAME = "CUSTOM copy 2";
var INNER_TEXT_LAYER_NAME = "Madafaka";
var STYLE_GROUPS = ["metal", "ALTIN", "MAVI", "PEMBE"];

function findLayerRecursive(container, targetName) {
  for (var i = 0; i < container.artLayers.length; i++) {
    if (container.artLayers[i].name === targetName) return container.artLayers[i];
  }
  for (var j = 0; j < container.layerSets.length; j++) {
    var found = findLayerRecursive(container.layerSets[j], targetName);
    if (found) return found;
  }
  return null;
}

function findFirstTextLayer(container) {
  for (var i = 0; i < container.artLayers.length; i++) {
    if (container.artLayers[i].kind === LayerKind.TEXT) return container.artLayers[i];
  }
  for (var j = 0; j < container.layerSets.length; j++) {
    var found = findFirstTextLayer(container.layerSets[j]);
    if (found) return found;
  }
  return null;
}

function findTopLevelGroup(doc, name) {
  for (var i = 0; i < doc.layerSets.length; i++) {
    if (doc.layerSets[i].name === name) return doc.layerSets[i];
  }
  return null;
}

function setVisibilityPolicy(doc) {
  if (TEXT_ONLY_MODE) {
    for (var i = 0; i < doc.layers.length; i++) {
      doc.layers[i].visible = false;
    }
  }

  for (var s = 0; s < STYLE_GROUPS.length; s++) {
    var group = findTopLevelGroup(doc, STYLE_GROUPS[s]);
    if (group) group.visible = (STYLE_GROUPS[s] === STYLE_GROUP_TO_USE);
  }
}

function exportPng24(documentRef, path) {
  var outFile = new File(path);
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(outFile, ExportType.SAVEFORWEB, opts);
}

try {
  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);

  var doc = app.open(psdFile);
  var baseState = doc.activeHistoryState;

  setVisibilityPolicy(doc);

  var styleGroup = findTopLevelGroup(doc, STYLE_GROUP_TO_USE);
  if (!styleGroup) throw new Error("Style group not found: " + STYLE_GROUP_TO_USE);

  var smartLayer = findLayerRecursive(styleGroup, SMART_LAYER_NAME);
  if (!smartLayer) throw new Error("Smart layer not found in style group: " + SMART_LAYER_NAME);

  doc.activeLayer = smartLayer;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var subDoc = app.activeDocument;
  var textLayer = findLayerRecursive(subDoc, INNER_TEXT_LAYER_NAME);
  if (!textLayer) textLayer = findFirstTextLayer(subDoc);
  if (!textLayer) throw new Error("No text layer inside selected smart object");

  textLayer.textItem.contents = RENDER_NAME;
  subDoc.save();
  subDoc.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  exportPng24(doc, OUTPUT_PATH);

  doc.activeHistoryState = baseState;
  doc.close(SaveOptions.DONOTSAVECHANGES);

  "DONE|" + OUTPUT_PATH + "|style=" + STYLE_GROUP_TO_USE + "|textOnly=" + TEXT_ONLY_MODE;
} catch (e) {
  try {
    if (app.documents.length > 0) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
  } catch (ignore) {}
  "FATAL|" + e;
}
