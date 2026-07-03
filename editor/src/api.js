const j = (r) => {
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

export const api = (u, o) => fetch(u, o).then(j);
export const jput = (u, b) =>
  fetch(u, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(j);
export const post = (u) => fetch(u, { method: "POST" }).then(j);

export async function pollJob(job, onStep, signal) {
  for (let misses = 0; ; ) {
    if (signal?.cancelled) return "cancelled";
    let s;
    try {
      s = await api(`/api/jobs/${job}`);
      misses = 0;
    } catch (e) {
      if (++misses >= 5) return "lost";
      await new Promise((r) => setTimeout(r, 2500));
      continue;
    }
    onStep?.(s);
    if (["done", "error", "cancelled"].includes(s.status)) return s.status;
    await new Promise((r) => setTimeout(r, 1400));
  }
}
