/*
  Batch renderer for text_only_2.psd
  - Uses each color style group (metal / ALTIN / MAVI / PEMBE)
  - Updates text inside that style's smart object
  - Exports PNG per (name, style)
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var PSD_PATH = "/Users/aydin/Desktop/apps/ps-automation/data/text_only_2.psd";
var OUTPUT_DIR = "/Users/aydin/Desktop/apps/ps-automation/output/text_only_2_batch";

// Either "ALL" or one of: metal, ALTIN, MAVI, PEMBE
var STYLE_MODE = "ALL";
var STYLE_GROUPS = ["metal", "ALTIN", "MAVI", "PEMBE"];

var NAMES = [
  "KEREM",
  "MERT",
  "ZEYNEP"
];

// Known structure hints (fallbacks exist)
var TEXT_SUBGROUP_NAME = "TEXT";
var SMART_LAYER_NAME = "CUSTOM copy 2";
var INNER_TEXT_LAYER_NAME = "Madafaka";

function sanitizeFilename(name) {
  var s = name.replace(/[^a-zA-Z0-9_-]/g, "_");
  s = s.replace(/_+/g, "_");
  s = s.replace(/^_+|_+$/g, "");
  if (s.length === 0) s = "untitled";
  return s;
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

function findTopLevelGroup(doc, name) {
  for (var i = 0; i < doc.layerSets.length; i++) {
    if (doc.layerSets[i].name === name) return doc.layerSets[i];
  }
  return null;
}

function setOnlyStyleVisible(doc, styleName) {
  for (var i = 0; i < STYLE_GROUPS.length; i++) {
    var g = findTopLevelGroup(doc, STYLE_GROUPS[i]);
    if (g) g.visible = (STYLE_GROUPS[i] === styleName);
  }
}

function exportPng24(documentRef, fileRef) {
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  documentRef.exportDocument(fileRef, ExportType.SAVEFORWEB, opts);
}

function getTargetStyles() {
  if (STYLE_MODE === "ALL") return STYLE_GROUPS;
  return [STYLE_MODE];
}

function resolveSmartLayer(styleGroup) {
  var textGroup = findLayerRecursive(styleGroup, TEXT_SUBGROUP_NAME);
  var scope = textGroup ? textGroup : styleGroup;
  var smart = findLayerRecursive(scope, SMART_LAYER_NAME);
  if (!smart) smart = findFirstSmartObject(scope);
  return smart;
}

try {
  var psdFile = new File(PSD_PATH);
  if (!psdFile.exists) throw new Error("PSD not found: " + PSD_PATH);

  var outFolder = new Folder(OUTPUT_DIR);
  if (!outFolder.exists) outFolder.create();

  var doc = app.open(psdFile);
  var baseState = doc.activeHistoryState;

  var styles = getTargetStyles();
  var success = 0;
  var failures = [];

  for (var n = 0; n < NAMES.length; n++) {
    for (var s = 0; s < styles.length; s++) {
      var currentName = NAMES[n];
      var currentStyle = styles[s];
      try {
        doc.activeHistoryState = baseState;
        setOnlyStyleVisible(doc, currentStyle);

        var styleGroup = findTopLevelGroup(doc, currentStyle);
        if (!styleGroup) throw new Error("Style group not found: " + currentStyle);

        var smart = resolveSmartLayer(styleGroup);
        if (!smart) throw new Error("Smart object not found in style: " + currentStyle);

        doc.activeLayer = smart;
        executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

        var sub = app.activeDocument;
        var textLayer = findLayerRecursive(sub, INNER_TEXT_LAYER_NAME);
        if (!textLayer) textLayer = findFirstTextLayer(sub);
        if (!textLayer) throw new Error("Text layer not found inside smart object");

        textLayer.textItem.contents = currentName;
        sub.save();
        sub.close(SaveOptions.SAVECHANGES);
        app.activeDocument = doc;

        var fileName = sanitizeFilename(currentName) + "_" + currentStyle + ".png";
        var outFile = new File(outFolder.fsName + "/" + fileName);
        exportPng24(doc, outFile);
        success++;
      } catch (e) {
        try {
          if (app.documents.length > 1) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
        } catch (ignore) {}
        app.activeDocument = doc;
        failures.push(currentName + "/" + currentStyle + " => " + e);
      }
    }
  }

  doc.activeHistoryState = baseState;
  doc.close(SaveOptions.DONOTSAVECHANGES);

  var msg = "Bitti\\nBasarili: " + success + "\\nHatali: " + failures.length + "\\nOutput: " + OUTPUT_DIR;
  if (failures.length > 0) msg += "\\n\\nHatalar:\\n- " + failures.join("\\n- ");
  alert(msg);
} catch (fatal) {
  alert("FATAL: " + fatal);
}
