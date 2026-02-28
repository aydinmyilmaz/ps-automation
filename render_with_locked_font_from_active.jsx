/*
  Render from currently open PSD state with locked typography.
  - Edits only active style group's TEXT > CUSTOM copy 2 smart object text
  - Forces exact font parameters before export
  - Stops with explicit error if required font is not installed
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var RENDER_NAME = "KEREM";
var OUTPUT_BASE = "/Users/aydin/Desktop/apps/ps-automation/output/KEREM_LOCKED_FONT_ONLY";
var SHOW_ALERT = false;

var STYLE_GROUPS = ["metal", "ALTIN", "MAVI", "PEMBE"];
var FORCE_STYLE = ""; // "" => detect visible style

// Typography lock (from your PSD)
var VERIFY_FONT = true;
var SET_FONT_IF_MISMATCH = false;
var FONT_POSTSCRIPT_NAME = "ClarendonBT-Black";
var LOCK_FULL_TYPOGRAPHY = false;
var FONT_SIZE_PT = 10.9510469055176;
var TRACKING = 0;
var HORIZONTAL_SCALE = 100;
var VERTICAL_SCALE = 100;
var FAUX_BOLD = false;
var FAUX_ITALIC = false;

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
  if (FORCE_STYLE) return FORCE_STYLE;
  for (var i = 0; i < STYLE_GROUPS.length; i++) {
    var g = findTopLevelGroup(doc, STYLE_GROUPS[i]);
    if (g && g.visible) return STYLE_GROUPS[i];
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

function addBlackBackgroundAtBottom(documentRef) {
  var bg = documentRef.artLayers.add();
  bg.name = "preview_black_bg";
  bg.move(documentRef.layers[documentRef.layers.length - 1], ElementPlacement.PLACEAFTER);
  documentRef.selection.selectAll();
  var black = new SolidColor();
  black.rgb.red = 0;
  black.rgb.green = 0;
  black.rgb.blue = 0;
  documentRef.selection.fill(black);
  documentRef.selection.deselect();
}

function fontExists(postScriptName) {
  for (var i = 0; i < app.fonts.length; i++) {
    if (app.fonts[i].postScriptName === postScriptName) return true;
  }
  return false;
}

function ensureTypography(textLayer) {
  var currentFont = textLayer.textItem.font;
  if (VERIFY_FONT && currentFont !== FONT_POSTSCRIPT_NAME) {
    if (!SET_FONT_IF_MISMATCH) {
      throw new Error("Font mismatch. Beklenen: " + FONT_POSTSCRIPT_NAME + " / Mevcut: " + currentFont);
    }
    textLayer.textItem.font = FONT_POSTSCRIPT_NAME;
  }

  if (LOCK_FULL_TYPOGRAPHY) {
    textLayer.textItem.size = FONT_SIZE_PT;
    textLayer.textItem.tracking = TRACKING;
    textLayer.textItem.horizontalScale = HORIZONTAL_SCALE;
    textLayer.textItem.verticalScale = VERTICAL_SCALE;
    textLayer.textItem.fauxBold = FAUX_BOLD;
    textLayer.textItem.fauxItalic = FAUX_ITALIC;
  }
}

try {
  if (app.documents.length === 0) throw new Error("Acik PSD yok.");
  if ((VERIFY_FONT || SET_FONT_IF_MISMATCH) && !fontExists(FONT_POSTSCRIPT_NAME)) {
    throw new Error("Gerekli font yuklu degil: " + FONT_POSTSCRIPT_NAME);
  }

  var doc = app.activeDocument;
  var baseState = doc.activeHistoryState;

  var styleName = detectVisibleStyle(doc);
  if (!styleName) throw new Error("Gorunur style bulunamadi (metal/ALTIN/MAVI/PEMBE).");

  var styleGroup = findTopLevelGroup(doc, styleName);
  if (!styleGroup) throw new Error("Style group yok: " + styleName);

  var textGroup = findLayerRecursive(styleGroup, "TEXT");
  var scope = textGroup ? textGroup : styleGroup;

  var smart = findLayerRecursive(scope, "CUSTOM copy 2");
  if (!smart) smart = findFirstSmartObject(scope);
  if (!smart) throw new Error("CUSTOM copy 2 (veya smart object) bulunamadi.");

  doc.activeLayer = smart;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var sub = app.activeDocument;
  var textLayer = findLayerRecursive(sub, "Madafaka");
  if (!textLayer) textLayer = findFirstTextLayer(sub);
  if (!textLayer) throw new Error("Smart object icinde text layer yok.");

  ensureTypography(textLayer);
  textLayer.textItem.contents = RENDER_NAME;

  sub.save();
  sub.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  var dup = doc.duplicate();
  dup.trim(TrimType.TRANSPARENT, true, true, true, true);
  exportPng24(dup, OUTPUT_BASE + "_transparent.png");
  addBlackBackgroundAtBottom(dup);
  exportPng24(dup, OUTPUT_BASE + "_black_preview.png");
  dup.close(SaveOptions.DONOTSAVECHANGES);

  doc.activeHistoryState = baseState;
  if (SHOW_ALERT) alert("Bitti: " + OUTPUT_BASE + "_transparent.png");
} catch (e) {
  if (SHOW_ALERT) {
    alert("Hata: " + e);
  } else {
    throw e;
  }
}
