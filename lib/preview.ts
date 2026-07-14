/**
 * Turn a half-streamed document into something safe to render.
 *
 * Rendering raw partial HTML is why a live preview looks broken mid-stream:
 * the browser paints the body before <style> has closed, so you get unstyled
 * content that then snaps into place. Worse, a half-written <script> executes
 * and throws.
 *
 * Returns null while the document isn't worth showing yet.
 */
export function renderablePartial(partial: string): string | null {
  let s = partial;

  // Nothing to show until the document has actually opened.
  if (!/<!DOCTYPE|<html/i.test(s)) return null;

  // THE IMPORTANT ONE: wait for the stylesheet to close. An unclosed <style>
  // means the page would paint naked and then reflow — worse than showing
  // nothing for another second.
  const opens = (s.match(/<style[\s>]/gi) ?? []).length;
  const closes = (s.match(/<\/style>/gi) ?? []).length;
  if (opens === 0 || opens > closes) return null;

  // Drop a trailing partial tag:  <div class="fo
  const lt = s.lastIndexOf("<");
  const gt = s.lastIndexOf(">");
  if (lt > gt) s = s.slice(0, lt);

  // Never execute a half-written script.
  const lastScript = s.toLowerCase().lastIndexOf("<script");
  if (lastScript !== -1 && !/<\/script>/i.test(s.slice(lastScript))) {
    s = s.slice(0, lastScript);
  }

  if (!/<\/body>/i.test(s)) s += "\n</body>";
  if (!/<\/html>/i.test(s)) s += "\n</html>";
  return s;
}
