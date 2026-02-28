/*
  Single-style fidelity test
  - One PSD
  - One style group (e.g. metal)
  - Update all text layers inside selected smart object
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = "/Users/aydin/Desktop/apps/ps-automation/data/text_only_2.psd";
var STYLE_NAME = "metal"; // metal / ALTIN / MAVI / PEMBE
var RENDER_NAME = "KEREM";
var OUTPUT_PATH = "/Users/aydin/Desktop/apps/ps-automation/output/KEREM_metal_fidelity.png";
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

function setAllTextLayers(container, value) {
  var changed = 0;
  for (var i = 0; i < container.artLayers.length; i++) {
    var l = container.artLayers[i];
    if (l.kind === LayerKind.TEXT) {
      l.textItem.contents = value;
      changed++;
    }
  }
  for (var j = 0; j < container.layerSets.length; j++) {
    changed += setAllTextLayers(container.layerSets[j], value);
  }
  return changed;
}

function setOnlyStyleVisible(doc, styleName) {
  for (var i = 0; i < STYLE_GROUPS.length; i++) {
    var g = findTopLevelGroup(doc, STYLE_GROUPS[i]);
    if (g) g.visible = (STYLE_GROUPS[i] === styleName);
  }
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
  var doc = app.open(new File(PSD_PATH));
  var base = doc.activeHistoryState;

  setOnlyStyleVisible(doc, STYLE_NAME);

  var styleGroup = findTopLevelGroup(doc, STYLE_NAME);
  if (!styleGroup) throw new Error("Style group not found: " + STYLE_NAME);

  var textGroup = findLayerRecursive(styleGroup, "TEXT");
  var scope = textGroup ? textGroup : styleGroup;
  var smart = findLayerRecursive(scope, "CUSTOM copy 2");
  if (!smart) smart = findFirstSmartObject(scope);
  if (!smart) throw new Error("Smart object not found in style: " + STYLE_NAME);

  doc.activeLayer = smart;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var sub = app.activeDocument;
  var changedInside = setAllTextLayers(sub, RENDER_NAME);
  if (changedInside === 0) throw new Error("No text layers found inside smart object.");
  sub.save();
  sub.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  exportPng24(doc, OUTPUT_PATH);

  doc.activeHistoryState = base;
  doc.close(SaveOptions.DONOTSAVECHANGES);

  alert("DONE\\nOutput: " + OUTPUT_PATH + "\\nChanged text layers in smart: " + changedInside);
} catch (e) {
  try {
    if (app.documents.length > 0) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
  } catch (ignore) {}
  alert("FATAL: " + e);
}
