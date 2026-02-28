/*
  Render from currently open PSD state.
  - Keeps your current visible/hidden layers exactly as-is.
  - Updates all text instances (direct + inside smart objects) to one name.
  - Exports PNG, then reverts by history state.
*/

#target photoshop
app.displayDialogs = DialogModes.NO;

var RENDER_NAME = "MERT";
var OUTPUT_PATH = "/Users/aydin/Desktop/apps/ps-automation/output/MERT_ACTIVE_DOC.png";

function exportPng24(doc, path) {
  var outFile = new File(path);
  var opts = new ExportOptionsSaveForWeb();
  opts.format = SaveDocumentType.PNG;
  opts.PNG8 = false;
  opts.transparency = true;
  opts.interlaced = false;
  doc.exportDocument(outFile, ExportType.SAVEFORWEB, opts);
}

function setAllTextInContainer(container, value) {
  var changed = 0;
  for (var i = 0; i < container.artLayers.length; i++) {
    var layer = container.artLayers[i];
    if (layer.kind === LayerKind.TEXT) {
      layer.textItem.contents = value;
      changed++;
    }
  }
  for (var j = 0; j < container.layerSets.length; j++) {
    changed += setAllTextInContainer(container.layerSets[j], value);
  }
  return changed;
}

function collectSmartLayers(container, acc) {
  for (var i = 0; i < container.artLayers.length; i++) {
    if (container.artLayers[i].kind === LayerKind.SMARTOBJECT) acc.push(container.artLayers[i]);
  }
  for (var j = 0; j < container.layerSets.length; j++) {
    collectSmartLayers(container.layerSets[j], acc);
  }
}

try {
  if (app.documents.length === 0) throw new Error("Acik PSD yok.");

  var doc = app.activeDocument;
  var baseState = doc.activeHistoryState;
  var directChanged = setAllTextInContainer(doc, RENDER_NAME);

  var smarts = [];
  collectSmartLayers(doc, smarts);

  var smartTextChanged = 0;
  for (var s = 0; s < smarts.length; s++) {
    try {
      doc.activeLayer = smarts[s];
      executeAction(stringIDToTypeID("placedLayerEditContents"), undefined, DialogModes.NO);
      var sub = app.activeDocument;

      var changedInSub = setAllTextInContainer(sub, RENDER_NAME);
      if (changedInSub > 0) {
        smartTextChanged += changedInSub;
        sub.save();
        sub.close(SaveOptions.SAVECHANGES);
      } else {
        sub.close(SaveOptions.DONOTSAVECHANGES);
      }

      app.activeDocument = doc;
    } catch (inner) {
      try {
        if (app.activeDocument != doc) app.activeDocument.close(SaveOptions.DONOTSAVECHANGES);
      } catch (ignore) {}
      app.activeDocument = doc;
    }
  }

  exportPng24(doc, OUTPUT_PATH);
  doc.activeHistoryState = baseState;

  alert(
    "Bitti\\n" +
    "Output: " + OUTPUT_PATH + "\\n" +
    "Direct text changed: " + directChanged + "\\n" +
    "Smart text changed: " + smartTextChanged
  );
} catch (e) {
  alert("Hata: " + e);
}
