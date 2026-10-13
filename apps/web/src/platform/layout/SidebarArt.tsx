/**
 * The sidebar's bubbles (ADR-059): a few soft bubbles of the app's color in the sidebar's top and bottom
 * corners, partly outside it. Empty elements that CSS paints (layout.css), so they follow the app's color;
 * hidden from assistive technology, never in the way of a click.
 */
export function SidebarArt() {
  return (
    <div className="sidebar-art" aria-hidden="true">
      <span className="bubble b1" />
      <span className="bubble b2" />
      <span className="bubble b3" />
      <span className="bubble b4" />
      <span className="bubble b5" />
    </div>
  );
}
