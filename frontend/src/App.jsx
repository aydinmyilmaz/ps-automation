import { useMemo, useState } from "react";

const STYLES = ["metal", "ALTIN", "MAVI", "PEMBE"];

export default function App() {
  const [text, setText] = useState("KEREM");
  const [style, setStyle] = useState("PEMBE");
  const [uppercase, setUppercase] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [imageUrl, setImageUrl] = useState("");

  const canRender = useMemo(() => text.trim().length > 0 && !loading, [text, loading]);

  async function onRender(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    setImageUrl("");

    try {
      const res = await fetch("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim(), style, uppercase })
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || "Render failed");
      }
      setImageUrl(data.image_url);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <div className="card">
        <h1>Single Name Renderer</h1>
        <p className="sub">Tek isim + tek renk render. Batch yok.</p>

        <form className="form" onSubmit={onRender}>
          <label className="field">
            <span>Text</span>
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Ornek: CHARLIE"
              maxLength={40}
            />
          </label>

          <label className="field">
            <span>Renk</span>
            <select value={style} onChange={(e) => setStyle(e.target.value)}>
              {STYLES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="fieldCheck">
            <input
              type="checkbox"
              checked={uppercase}
              onChange={(e) => setUppercase(e.target.checked)}
            />
            <span>Uppercase</span>
          </label>

          <button disabled={!canRender} type="submit">
            {loading ? "Rendering..." : "Render PNG"}
          </button>
        </form>

        {error && <p className="error">{error}</p>}

        <div className="previewWrap">
          {imageUrl ? (
            <>
              <img className="previewImg" src={imageUrl} alt="render result" />
              <a className="download" href={imageUrl} download>
                Download PNG
              </a>
            </>
          ) : (
            <div className="placeholder">Render sonrası görsel burada çıkacak.</div>
          )}
        </div>
      </div>
    </div>
  );
}
