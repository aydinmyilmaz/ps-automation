/*
  Reference-match renderer from active PSD state.
  Expected structure in selected style group:
    Color Fill 1
    Curves 1
    TEXT (group with Stroke + Outer Glow effects)
      CUSTOM copy 2 (smart object with Bevel & Emboss + Gradient Overlay)
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var RENDER_NAME = "KEREM";
var OUTPUT_BASE = "/Users/aydin/Desktop/apps/ps-automation/output/KEREM_refmatch";
var STYLE_GROUPS = ["metal", "ALTIN", "MAVI", "PEMBE"];
var FORCE_STYLE = ""; // "" means auto-detect visible style group
var INNER_TEXT_LAYER_NAME = "Madafaka";
var SHOW_ALERT = false;

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

function hasLayerEffects(layer) {
  try {
    var ref = new ActionReference();
    ref.putIdentifier(charIDToTypeID("Lyr "), layer.id);
    var desc = executeActionGet(ref);
    return desc.hasKey(stringIDToTypeID("layerEffects"));
  } catch (e) {
    return false;
  }
}

function detectStyle(doc) {
  if (FORCE_STYLE) return FORCE_STYLE;
  for (var i = 0; i < STYLE_GROUPS.length; i++) {
    var g = findTopLevelGroup(doc, STYLE_GROUPS[i]);
    if (g && g.visible) return STYLE_GROUPS[i];
  }
  return null;
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
  if (app.documents.length === 0) throw new Error("Acik PSD yok.");

  var doc = app.activeDocument;
  var baseState = doc.activeHistoryState;

  var styleName = detectStyle(doc);
  if (!styleName) throw new Error("Gorunur style yok. metal/ALTIN/MAVI/PEMBE gruplarindan birini ac.");

  var styleGroup = findTopLevelGroup(doc, styleName);
  if (!styleGroup) throw new Error("Style group bulunamadi: " + styleName);

  var textGroup = null;
  for (var i = 0; i < styleGroup.layerSets.length; i++) {
    if (styleGroup.layerSets[i].name === "TEXT") {
      textGroup = styleGroup.layerSets[i];
      break;
    }
  }
  if (!textGroup) throw new Error("TEXT grubu bulunamadi: " + styleName);

  var smart = findLayerRecursive(textGroup, "CUSTOM copy 2");
  if (!smart) throw new Error("CUSTOM copy 2 bulunamadi: " + styleName + "/TEXT");

  var textGroupHasFx = hasLayerEffects(textGroup);
  var smartHasFx = hasLayerEffects(smart);

  doc.activeLayer = smart;
  executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);

  var sub = app.activeDocument;
  var textLayer = findLayerRecursive(sub, INNER_TEXT_LAYER_NAME);
  if (!textLayer) textLayer = findFirstTextLayer(sub);
  if (!textLayer) throw new Error("Smart object icinde text layer yok.");

  textLayer.textItem.contents = RENDER_NAME;
  sub.save();
  sub.close(SaveOptions.SAVECHANGES);
  app.activeDocument = doc;

  // Transparent trimmed output
  var dup = doc.duplicate();
  dup.trim(TrimType.TRANSPARENT, true, true, true, true);
  exportPng24(dup, OUTPUT_BASE + "_transparent.png");

  // Black background preview output (for visual diff vs reference)
  var bg = dup.artLayers.add();
  bg.name = "preview_black_bg";
  bg.move(dup.layers[dup.layers.length - 1], ElementPlacement.PLACEAFTER);
  dup.selection.selectAll();
  var black = new SolidColor();
  black.rgb.red = 0;
  black.rgb.green = 0;
  black.rgb.blue = 0;
  dup.selection.fill(black);
  dup.selection.deselect();
  exportPng24(dup, OUTPUT_BASE + "_black_preview.png");
  dup.close(SaveOptions.DONOTSAVECHANGES);

  doc.activeHistoryState = baseState;

  if (SHOW_ALERT) {
    alert(
      "Bitti\\n" +
      "Style: " + styleName + "\\n" +
      "TEXT group FX: " + textGroupHasFx + "\\n" +
      "SMART FX: " + smartHasFx + "\\n" +
      "Out (transparent): " + OUTPUT_BASE + "_transparent.png\\n" +
      "Out (black): " + OUTPUT_BASE + "_black_preview.png"
    );
  }
} catch (e) {
  if (SHOW_ALERT) {
    alert("Hata: " + e);
  } else {
    throw e;
  }
}
