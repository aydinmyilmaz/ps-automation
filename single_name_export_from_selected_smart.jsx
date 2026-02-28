/*
  Single Name Export from Selected Smart Object
  - Use this when title text is inside a Smart Object.
  - Select the correct smart object layer in Layers panel first.
  - Script edits text inside that smart object, keeps all visual effects in parent PSD,
    exports one PNG, then restores parent document history.
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var RENDER_NAME = "KEREM";
var OUTPUT_PATH = "/Users/aydin/Desktop/apps/ps-automation/output/KEREM_FINAL.png";
var INNER_TEXT_LAYER_NAME = "Madafaka"; // fallback: first text layer if not found

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

function exportPng24(documentRef, outputPath) {
  var outFile = new File(outputPath);
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

  var selected = doc.activeLayer;
  if (!selected) throw new Error("Layer secili degil.");
  if (selected.kind !== LayerKind.SMARTOBJECT) {
    throw new Error("Secili layer Smart Object degil: " + selected.name);
  }

  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);
  var subDoc = app.activeDocument;

  var textLayer = findLayerRecursive(subDoc, INNER_TEXT_LAYER_NAME);
  if (!textLayer) textLayer = findFirstTextLayer(subDoc);
  if (!textLayer) {
    subDoc.close(SaveOptions.DONOTSAVECHANGES);
    throw new Error("Smart Object icinde text layer bulunamadi.");
  }

  textLayer.textItem.contents = RENDER_NAME;
  subDoc.save();
  subDoc.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  exportPng24(doc, OUTPUT_PATH);

  // Keep PSD unchanged in memory for repeated runs.
  doc.activeHistoryState = baseState;

  "DONE|" + OUTPUT_PATH + "|layer=" + selected.name + "|text=" + textLayer.name;
} catch (e) {
  "FATAL|" + e;
}
