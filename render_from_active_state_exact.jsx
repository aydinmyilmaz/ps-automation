/*
  Render from the currently open PSD state (including unsaved changes).
  - Detects currently visible style group (metal/ALTIN/MAVI/PEMBE)
  - Edits only that style's text smart object
  - Exports trimmed PNG
  - Reverts by history state (doc remains open)
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var RENDER_NAME = "MERT";
var OUTPUT_PATH = "/Users/aydin/Desktop/apps/ps-automation/output/MERT_ACTIVE_EXACT.png";
var STYLE_GROUPS = ["metal", "ALTIN", "MAVI", "PEMBE"];

function findTopLevelGroup(doc, name) {
  for (var i = 0; i < doc.layerSets.length; i++) {
    if (doc.layerSets[i].name === name) return doc.layerSets[i];
  }
  return null;
}

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

function findFirstSmartObject(container) {
  for (var i = 0; i < container.artLayers.length; i++) {
    if (container.artLayers[i].kind === LayerKind.SMARTOBJECT) return container.artLayers[i];
  }
  for (var j = 0; j < container.layerSets.length; j++) {
    var found = findFirstSmartObject(container.layerSets[j]);
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

function detectVisibleStyle(doc) {
  for (var i = 0; i < STYLE_GROUPS.length; i++) {
    var group = findTopLevelGroup(doc, STYLE_GROUPS[i]);
    if (group && group.visible) return STYLE_GROUPS[i];
  }
  return null;
}

function exportPng24(documentRef, filePath) {
  var outFile = new File(filePath);
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(outFile, ExportType.SAVEFORWEB, opts);
}

try {
  if (app.documents.length === 0) throw new Error("Acik PSD yok.");

  var doc = app.activeDocument;
  var baseState = doc.activeHistoryState;

  var styleName = detectVisibleStyle(doc);
  if (!styleName) throw new Error("Gorunur style grubu bulunamadi (metal/ALTIN/MAVI/PEMBE).");

  var styleGroup = findTopLevelGroup(doc, styleName);
  var textGroup = findLayerRecursive(styleGroup, "TEXT");
  var scope = textGroup ? textGroup : styleGroup;

  var smart = findLayerRecursive(scope, "CUSTOM copy 2");
  if (!smart) smart = findFirstSmartObject(scope);
  if (!smart) throw new Error("Smart object bulunamadi: " + styleName);

  doc.activeLayer = smart;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var subDoc = app.activeDocument;
  var textLayer = findLayerRecursive(subDoc, "Madafaka");
  if (!textLayer) textLayer = findFirstTextLayer(subDoc);
  if (!textLayer) throw new Error("Smart object icinde text layer bulunamadi.");

  textLayer.textItem.contents = RENDER_NAME;
  subDoc.save();
  subDoc.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  // Export from duplicate to keep open doc state untouched.
  var dup = doc.duplicate();
  dup.trim(TrimType.TRANSPARENT, true, true, true, true);
  exportPng24(dup, OUTPUT_PATH);
  dup.close(SaveOptions.DONOTSAVECHANGES);

  doc.activeHistoryState = baseState;

  alert("Bitti\\nStyle: " + styleName + "\\nOutput: " + OUTPUT_PATH);
} catch (e) {
  alert("Hata: " + e);
}
