/**
 * The sidebar's art (ADR-058): an aurora with dots and two beams of light at the top, three waves with a
 * neon crest at the foot, a neon edge. Empty elements that CSS paints (layout.css) in shades of the app's
 * color, so the art follows it; hidden from assistive technology, never in the way of a click.
 */
export function SidebarArt() {
  return (
    <div className="sidebar-art" aria-hidden="true">
      <span className="art-aurora" />
      <span className="art-beams" />
      <span className="art-waves">
        <i className="w1" />
        <i className="w2" />
        <i className="w3" />
        <i className="glow" />
        <i className="crest" />
      </span>
      <span className="art-edge" />
    </div>
  );
}
