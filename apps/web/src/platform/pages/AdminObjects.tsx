/** The Objects Warehouse (ADR-055; named so in ADR-057): the built-in library and the warehouse that
 * cost-saver lessons place drawings from. Review what the harvest collected from finished lessons,
 * approve only what is exactly right (with the words it is used for), reject many at once from the cards,
 * import SVG drawings, and try which drawings a scene brief would be offered. A drawing reaches lessons
 * only once approved; each action needs its permission (the API checks). */
import { EDUCATIONAL_CLEAN, EDUCATIONAL_CLEAN_DARK, isHexColor, resolveColor, type Theme } from "@animator/engine";
import {
  Archive,
  Check,
  CircleCheck,
  FlaskConical,
  Layers,
  Search,
  Shapes,
  Sparkles,
  Trash2,
  TriangleAlert,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import { type ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  type FigureArt,
  type ObjectEdit,
  type ObjectMatch,
  type ObjectStatus,
  type ObjectView,
  type SavedObjectDetail,
  type SavedObjectSummary,
  v1,
} from "../api";
import { useAuth } from "../auth";
import { ago, plural } from "../format";
import { invalidate, useDebounced, useResource, useTitle } from "../hooks";
import { Link } from "../nav";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  EmptyState,
  SearchBox,
  Segmented,
  SelectField,
  Spinner,
  Stat,
  Tabs,
  TextArea,
  TextField,
} from "../ui/core";
import { Confirm, Modal, useToast } from "../ui/overlay";
import { AllCheck, BulkBar, RowCheck, useSelection } from "../ui/table";
import { Forbidden, Pager } from "./Admin";

const PAGE = 48;

const VIEWS: { value: ObjectView; label: string; count?: (c: Record<ObjectStatus, number>, lib: number) => number }[] =
  [
    { value: "candidate", label: "Waiting for review", count: (c) => c.candidate },
    { value: "approved", label: "In use", count: (c) => c.approved },
    { value: "library", label: "Built-in", count: (_c, lib) => lib },
    { value: "rejected", label: "Rejected", count: (c) => c.rejected },
    { value: "retired", label: "Retired", count: (c) => c.retired },
    { value: "all", label: "All" },
  ];

const SORTS = [
  { value: "score", label: "Best checks first" },
  { value: "new", label: "Newest" },
  { value: "name", label: "Name" },
  { value: "uses", label: "Most used" },
];

const STATUS: Record<ObjectStatus, { label: string; tone: "success" | "warning" | "danger" | "neutral" }> = {
  approved: { label: "In use", tone: "success" },
  candidate: { label: "Waiting for review", tone: "warning" },
  rejected: { label: "Rejected", tone: "danger" },
  retired: { label: "Retired", tone: "neutral" },
};

const ORIGIN: Record<SavedObjectSummary["origin"], string> = {
  library: "Built-in",
  harvest: "From a lesson",
  import: "Imported",
};

// ---------------------------------------------------------------------- the drawing

function paint(theme: Theme, ref: string | undefined): string {
  if (!ref || ref === "none") return "none";
  return isHexColor(ref) ? ref : resolveColor(theme, ref);
}

/** A saved drawing as lessons show it (the engine's colors for the theme), optionally with its parts'
 * anchors: where a label's leader line ends when a scene points at a part. */
export function FigurePreview({
  art,
  width,
  dark,
  anchors = false,
  label,
}: {
  art: FigureArt;
  width: number;
  dark: boolean;
  anchors?: boolean;
  label: string;
}) {
  const theme = dark ? EDUCATIONAL_CLEAN_DARK : EDUCATIONAL_CLEAN;
  const k = width / art.w;
  const shapes = useMemo(
    () =>
      art.shapes.map((s, i) => {
        const stroke = paint(theme, s.stroke);
        return (
          <path
            // biome-ignore lint/suspicious/noArrayIndexKey: the shapes of a drawing never reorder
            key={i}
            d={s.d}
            fill={paint(theme, s.fill)}
            fillRule={s.fillRule}
            stroke={stroke}
            strokeWidth={stroke === "none" ? undefined : (s.strokeWidth ?? 2)}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={s.opacity}
          />
        );
      }),
    [art, theme],
  );
  return (
    <svg
      className="figure-preview"
      role="img"
      aria-label={label}
      width={width}
      height={art.h * k}
      viewBox={`0 0 ${art.w} ${art.h}`}
    >
      {shapes}
      {anchors &&
        (art.anchors ?? []).map((a) => (
          <g key={a.name}>
            <circle cx={a.x} cy={a.y} r={4.5 / k} className="figure-anchor" strokeWidth={1.5 / k} />
            <text x={a.x + 7 / k} y={a.y - 5 / k} fontSize={12 / k} className="figure-anchor-label" strokeWidth={3 / k}>
              {a.name}
            </text>
          </g>
        ))}
    </svg>
  );
}

function pageIsDark(): boolean {
  return typeof document !== "undefined" && document.documentElement.dataset.theme === "dark";
}

// ---------------------------------------------------------------------- the page

export function AdminObjects() {
  useTitle("Objects Warehouse");
  const { can } = useAuth();
  const allowed = can("objects:read");
  const curate = can("objects:curate");
  const toast = useToast();
  const [view, setView] = useState<ObjectView>("candidate");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("score");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [dark, setDark] = useState(pageIsDark);
  const [importing, setImporting] = useState(false);
  const [trying, setTrying] = useState(false);
  const [harvest, setHarvest] = useState(false);
  const [harvesting, setHarvesting] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [rejectNote, setRejectNote] = useState("");
  const [rejectBusy, setRejectBusy] = useState(false);
  const term = useDebounced(q, 250);
  // biome-ignore lint/correctness/useExhaustiveDependencies: back to the first page when these change
  useEffect(() => setOffset(0), [view, term, sort]);
  const stats = useResource(allowed ? "admin:objects:stats" : null, v1.objectStats, 30_000);
  const list = useResource(allowed ? `admin:objects:list:${view}:${term}:${sort}:${offset}` : null, () =>
    v1.objects({ view, q: term || undefined, sort, offset, limit: PAGE }),
  );
  // Waiting for review: choose drawings on their cards and reject them together (approving stays one
  // at a time: each needs its words checked).
  const choosing = curate && view === "candidate";
  const items = list.data?.items ?? [];
  const selection = useSelection(choosing ? items.map((o) => o.id) : []);
  if (!allowed) return <Forbidden />;
  const changed = () => {
    void stats.reload();
    invalidate("admin:objects:");
  };
  const s = stats.data;
  const counts = list.data?.counts;
  const rejectChosen = async () => {
    setRejectBusy(true);
    try {
      const r = await v1.rejectObjects(selection.ids, rejectNote.trim());
      const skipped = r.skipped.length
        ? ` ${plural(r.skipped.length, "drawing")} skipped: ${[...new Set(r.skipped.map((x) => x.reason))].join(" ")}`
        : "";
      const text = `Rejected ${plural(r.changed.length, "drawing")}.${skipped}`;
      if (skipped) toast.info(text);
      else toast.success(text);
      selection.clear();
      setRejecting(false);
      setRejectNote("");
      changed();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRejectBusy(false);
    }
  };
  const runHarvest = async () => {
    setHarvesting(true);
    try {
      const r = await v1.harvestObjects(40);
      toast.success(
        r.lessons
          ? `Read ${plural(r.lessons, "lesson")}: ${plural(r.added.length, "new drawing")} to review.`
          : "Every finished lesson has been read already.",
      );
      setHarvest(false);
      changed();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setHarvesting(false);
    }
  };
  return (
    <div className="page admin objects-admin">
      <header className="page-head">
        <div>
          <p className="page-kicker">Administration</p>
          <h1>Objects Warehouse</h1>
          <p className="page-sub">
            Ready-made drawings that cost-saver lessons place where a scene shows exactly that thing (everything else is
            drawn). Built-in objects are ready; objects collected from finished lessons wait here until you approve
            them, with the words that show a lesson is about them. Reject several at once by choosing their cards.
          </p>
        </div>
        <div className="page-head-actions">
          <Button icon={FlaskConical} onClick={() => setTrying(true)}>
            Try a scene
          </Button>
          {curate && (
            <>
              <Button icon={Upload} onClick={() => setImporting(true)}>
                Import SVG
              </Button>
              <Button variant="primary" icon={Sparkles} onClick={() => setHarvest(true)}>
                Collect from lessons
              </Button>
            </>
          )}
        </div>
      </header>

      {s ? (
        <div className="stat-row">
          <Stat
            icon={CircleCheck}
            label="In use"
            value={s.by_status.approved}
            sub="offered to lessons"
            tone="success"
          />
          <Stat
            icon={Layers}
            label="Waiting for review"
            value={s.by_status.candidate}
            sub={`${s.by_origin.harvest} from lessons · ${s.by_origin.import} imported`}
            tone={s.by_status.candidate ? "warning" : "neutral"}
          />
          <Stat icon={Shapes} label="Built-in" value={s.by_origin.library} sub="drawn for the product" tone="brand" />
          <Stat icon={Check} label="Placed in scenes" value={s.placed} sub="by cost-saver lessons" tone="info" />
          <Stat
            icon={Sparkles}
            label="Lessons read"
            value={s.harvest.lessons_read}
            sub={s.harvest.last_run ? `last ${ago(s.harvest.last_run)}` : "not yet"}
            tone="neutral"
          />
        </div>
      ) : stats.error ? (
        <Alert tone="danger">{stats.error.message}</Alert>
      ) : (
        <Spinner />
      )}
      {s && s.errors.length > 0 && (
        <Alert tone="warning" title="Some drawing files could not be read">
          <ul className="plain-list">
            {s.errors.slice(0, 5).map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </Alert>
      )}

      <div className="toolbar">
        <Tabs
          label="Drawings"
          value={view}
          onChange={setView}
          tabs={VIEWS.map((v) => ({
            value: v.value,
            label: v.label,
            count: counts && v.count ? v.count(counts, list.data?.library ?? 0) : undefined,
          }))}
        />
      </div>
      <div className="toolbar">
        <SearchBox value={q} onChange={setQ} placeholder="Search names" />
        <div className="toolbar-right">
          <Segmented
            label="Preview"
            value={dark ? "dark" : "light"}
            onChange={(v) => setDark(v === "dark")}
            options={[
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
            ]}
          />
          <SelectField aria-label="Order" value={sort} onChange={(e) => setSort(e.target.value)} options={SORTS} />
        </div>
      </div>

      {choosing && items.length > 0 && (
        <div className="objects-choose">
          <AllCheck
            selection={selection}
            text={selection.all ? "All on this page chosen" : "Choose all on this page"}
          />
          <span className="muted small">
            Choose the drawings that are not right and reject them together; approve the good ones one by one.
          </span>
        </div>
      )}
      {list.error && <Alert tone="danger">{list.error.message}</Alert>}
      {list.loading && !list.data && <Spinner />}
      {list.data && list.data.items.length === 0 && (
        <EmptyState
          icon={Shapes}
          title="Nothing here"
          text={
            view === "candidate"
              ? "No drawing is waiting for review. Collect from lessons, or import an SVG."
              : "No drawing matches."
          }
        />
      )}
      <div className={`objects-grid ${dark ? "dark-preview" : ""} ${choosing ? "choosing" : ""}`}>
        {items.map((o) => (
          <div key={o.id} className={`object-card-wrap ${selection.has(o.id) ? "chosen" : ""}`}>
            {choosing && (
              <span className="object-pick">
                <RowCheck selection={selection} id={o.id} label={`Choose ${o.title}`} />
              </span>
            )}
            <button type="button" className="object-card" onClick={() => setOpen(o.id)}>
              <span className="object-card-art">
                <FigurePreview art={o.art} width={o.aspect > 1 ? 150 / o.aspect : 150} dark={dark} label={o.title} />
              </span>
              <span className="object-card-body">
                <b>{o.title}</b>
                <small>
                  {o.concept}
                  {o.view ? ` · ${o.view}` : ""}
                </small>
                <span className="object-card-tags">
                  <Badge tone={STATUS[o.status].tone}>{STATUS[o.status].label}</Badge>
                  <Badge tone="neutral">{ORIGIN[o.origin]}</Badge>
                </span>
                <small className="muted">
                  {o.score != null ? `checks ${Math.round(o.score * 100)}%` : "not checked"} · {plural(o.parts, "part")}{" "}
                  · {o.uses ? `placed ${plural(o.uses, "time")}` : "not placed yet"}
                </small>
              </span>
            </button>
          </div>
        ))}
      </div>
      <Pager offset={offset} size={PAGE} total={list.data?.total ?? 0} onChange={setOffset} />
      <BulkBar count={selection.count} noun={["drawing", "drawings"]} onClear={selection.clear}>
        <Button size="sm" variant="danger" icon={X} onClick={() => setRejecting(true)}>
          Reject
        </Button>
      </BulkBar>
      <Confirm
        open={rejecting}
        onClose={() => setRejecting(false)}
        onConfirm={() => void rejectChosen()}
        busy={rejectBusy}
        danger
        title={`Reject ${plural(selection.count, "drawing")}?`}
        text="They are not offered to lessons, and the harvest does not collect them again. You can restore any of them from Rejected."
        confirmLabel="Reject"
      >
        <TextArea
          label="Review note (optional)"
          rows={2}
          maxLength={500}
          value={rejectNote}
          onChange={(e) => setRejectNote(e.target.value)}
          placeholder="e.g. not the real thing, only shapes"
        />
      </Confirm>

      {open && <ObjectDetail id={open} dark={dark} onClose={() => setOpen(null)} onChanged={changed} curate={curate} />}
      {importing && (
        <ImportDialog
          onClose={() => setImporting(false)}
          onDone={(id) => {
            setImporting(false);
            changed();
            setView("candidate");
            setOpen(id);
          }}
        />
      )}
      {trying && <TryScene onClose={() => setTrying(false)} onOpen={(id) => setOpen(id)} />}
      <Confirm
        open={harvest}
        onClose={() => setHarvest(false)}
        onConfirm={() => void runHarvest()}
        busy={harvesting}
        title="Collect drawings from finished lessons?"
        text="Reads up to 40 finished lessons not read before and keeps the drawings built from shapes that pass every check, to review here. Nothing is used before you approve it, and no lesson changes."
        confirmLabel="Collect"
      />
    </div>
  );
}

// ---------------------------------------------------------------------- one drawing

const words = (list: string[]) => list.join(", ");
const unwords = (text: string) =>
  text
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean);

function ObjectDetail({
  id,
  dark,
  onClose,
  onChanged,
  curate,
}: {
  id: string;
  dark: boolean;
  onClose: () => void;
  onChanged: () => void;
  curate: boolean;
}) {
  const toast = useToast();
  const res = useResource(`admin:objects:one:${id}`, () => v1.object(id), 5_000);
  const [form, setForm] = useState<Record<string, string>>({});
  const [parts, setParts] = useState<Record<string, string>>({});
  const [anchors, setAnchors] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const o = res.data;
  useEffect(() => {
    if (!o) return;
    setForm({
      title: o.title,
      concept: o.concept,
      view: o.view,
      description: o.description,
      aliases: words(o.aliases),
      context: words(o.context),
      not_for: words(o.not_for),
      prefer: words(o.prefer),
      size_min: String(o.size.min),
      size_default: String(o.size.default),
      size_max: String(o.size.max),
      note: o.note,
    });
    setParts(o.parts);
  }, [o]);

  const field = (name: string) => ({
    value: form[name] ?? "",
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [name]: e.target.value })),
    disabled: !curate,
  });

  const act = async (name: string, run: () => Promise<SavedObjectDetail | { ok: boolean }>, done: string) => {
    setBusy(name);
    try {
      await run();
      toast.success(done);
      invalidate(`admin:objects:one:${id}`);
      await res.reload();
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const save = () => {
    const body: ObjectEdit = {
      title: form.title,
      concept: form.concept,
      view: form.view,
      description: form.description,
      aliases: unwords(form.aliases ?? ""),
      context: unwords(form.context ?? ""),
      not_for: unwords(form.not_for ?? ""),
      prefer: unwords(form.prefer ?? ""),
      parts,
      size: { min: Number(form.size_min), default: Number(form.size_default), max: Number(form.size_max) },
      note: form.note,
    };
    return act("save", () => v1.editObject(id, body), "Saved.");
  };

  return (
    <Modal open onClose={onClose} title={o ? o.title : "Object"} size="lg" description={o ? o.ref : undefined}>
      {!o ? (
        res.error ? (
          <Alert tone="danger">{res.error.message}</Alert>
        ) : (
          <Spinner />
        )
      ) : (
        <div className="object-detail">
          <div className={`object-detail-art ${dark ? "dark-preview" : ""}`}>
            <FigurePreview
              art={o.art}
              width={o.art.w >= o.art.h ? 380 : 380 * (o.art.w / o.art.h)}
              dark={dark}
              anchors={anchors}
              label={o.title}
            />
            <Checkbox checked={anchors} onChange={setAnchors}>
              Show where labels point (each part's anchor)
            </Checkbox>
            <div className="object-detail-tags">
              <Badge tone={STATUS[o.status].tone}>{STATUS[o.status].label}</Badge>
              <Badge tone="neutral">{ORIGIN[o.origin]}</Badge>
              <Badge tone="info">{o.uses ? `placed ${plural(o.uses, "time")}` : "not placed yet"}</Badge>
            </div>
            {o.source.generation_id && (
              <p className="small muted">
                Drawn in <Link href={`/lessons/${o.source.generation_id}`}>{o.source.lesson_title || "a lesson"}</Link>{" "}
                ({o.source.scene_id}, {o.source.object_id})
              </p>
            )}
            {o.quality && (
              <ul className="object-checks">
                {o.quality.checks.map((c) => (
                  <li key={c.name} className={c.ok ? "ok" : "bad"}>
                    {c.ok ? <Check size={14} /> : <X size={14} />}
                    <span>
                      {c.name.replaceAll("_", " ")}
                      {c.detail ? <small> — {c.detail}</small> : null}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="object-detail-form">
            {o.cautions.length > 0 && (
              <Alert tone="warning" title="Before it is used">
                <ul className="plain-list">
                  {o.cautions.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {o.import_warnings && o.import_warnings.length > 0 && (
              <Alert tone="info" title="Left out when imported">
                {o.import_warnings.join("; ")}
              </Alert>
            )}
            <div className="form-row">
              <TextField label="Title" maxLength={80} {...field("title")} />
              <TextField label="What it is" hint="e.g. human heart" maxLength={80} {...field("concept")} />
            </div>
            <TextField label="View" hint="e.g. cut open, from the front" maxLength={40} {...field("view")} />
            <TextArea
              label="What the drawing shows"
              hint="The scene writer reads this to decide whether it is exactly what the scene shows."
              rows={3}
              maxLength={300}
              {...field("description")}
            />
            <TextField
              label="Names (comma separated)"
              hint="A scene brief must use one of these, as whole words, for the drawing to be offered."
              {...field("aliases")}
            />
            <TextField
              label="Context words"
              hint="Word beginnings of which one must appear in the scene or the lesson (blood, pump, circulat)."
              {...field("context")}
            />
            <TextField
              label="Never for"
              hint="Phrases that mean something else (heart shape, computer virus): with any of them, it is not offered."
              {...field("not_for")}
            />
            <TextField
              label="Preferred when"
              hint="Words that make this drawing the better of two of the same thing (chamber, valve)."
              {...field("prefer")}
            />
            <div className="form-row three">
              <TextField label="Smallest width" type="number" min={24} max={1500} {...field("size_min")} />
              <TextField label="Usual width" type="number" min={24} max={1500} {...field("size_default")} />
              <TextField label="Largest width" type="number" min={24} max={1500} {...field("size_max")} />
            </div>
            {Object.keys(parts).length > 0 && (
              <div className="object-parts">
                <span className="ui-label">Part labels (what a scene calls each part)</span>
                {Object.entries(parts).map(([part, label]) => (
                  <label key={part} className="object-part">
                    <code>{part}</code>
                    <span className="ui-input">
                      <input
                        value={label}
                        disabled={!curate}
                        maxLength={60}
                        onChange={(e) => setParts((p) => ({ ...p, [part]: e.target.value }))}
                      />
                    </span>
                  </label>
                ))}
              </div>
            )}
            <TextArea label="Review note" rows={2} maxLength={500} {...field("note")} />
            {curate && (
              <div className="object-actions">
                <Button variant="secondary" loading={busy === "save"} onClick={() => void save()}>
                  Save changes
                </Button>
                {o.status !== "approved" && o.status !== "retired" && (
                  <Button
                    variant="primary"
                    icon={CircleCheck}
                    loading={busy === "approve"}
                    disabled={!o.approvable}
                    title={o.why_not ?? undefined}
                    onClick={() =>
                      void act(
                        "approve",
                        () => v1.reviewObject(id, "approve", form.note),
                        "Approved: lessons can use it.",
                      )
                    }
                  >
                    Approve
                  </Button>
                )}
                {o.status === "candidate" && (
                  <Button
                    variant="danger"
                    icon={X}
                    loading={busy === "reject"}
                    onClick={() => void act("reject", () => v1.reviewObject(id, "reject", form.note), "Rejected.")}
                  >
                    Reject
                  </Button>
                )}
                {o.status === "approved" && (
                  <Button
                    icon={Archive}
                    loading={busy === "retire"}
                    onClick={() =>
                      void act("retire", () => v1.reviewObject(id, "retire", form.note), "Retired: no longer offered.")
                    }
                  >
                    Retire
                  </Button>
                )}
                {(o.status === "rejected" || o.status === "retired") && (
                  <Button
                    icon={Undo2}
                    loading={busy === "restore"}
                    onClick={() => void act("restore", () => v1.reviewObject(id, "restore", form.note), "Restored.")}
                  >
                    Restore
                  </Button>
                )}
                {o.origin === "library" ? (
                  <Button
                    variant="ghost"
                    icon={Undo2}
                    loading={busy === "reset"}
                    onClick={() => void act("reset", () => v1.resetObject(id), "Back to how it ships.")}
                  >
                    Reset to built-in
                  </Button>
                ) : (
                  <Button variant="ghost" icon={Trash2} onClick={() => setConfirmDelete(true)}>
                    Delete
                  </Button>
                )}
              </div>
            )}
            {curate && o.why_not && o.status === "candidate" && (
              <p className="small muted">
                <TriangleAlert size={13} /> {o.why_not}
              </p>
            )}
          </div>
        </div>
      )}
      <Confirm
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() =>
          void act("delete", () => v1.deleteObject(id), "Deleted.").then(() => {
            setConfirmDelete(false);
            onClose();
          })
        }
        danger
        title="Delete this drawing?"
        text="It is removed from the warehouse. Lessons that already placed it keep it (they carry the drawing)."
        confirmLabel="Delete"
      />
    </Modal>
  );
}

// ---------------------------------------------------------------------- importing, trying

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const toast = useToast();
  const [svg, setSvg] = useState("");
  const [file, setFile] = useState("");
  const [title, setTitle] = useState("");
  const [concept, setConcept] = useState("");
  const [aliases, setAliases] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pick = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 600_000) {
      setError("The file is larger than 600 KB.");
      return;
    }
    setFile(f.name);
    setSvg(await f.text());
    if (!title) setTitle(f.name.replace(/\.svg$/i, "").replace(/[-_]+/g, " "));
  };
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const got = await v1.importObject({
        svg,
        title: title.trim(),
        concept: concept.trim(),
        aliases: unwords(aliases),
        description: description.trim(),
        file,
      });
      toast.success("Imported: review it before approving.");
      onDone(got.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      title="Import an SVG drawing"
      description="Shapes are imported (text, images and scripts are left out); it waits for review like any new drawing. Name parts with data-part, colors with data-fill or data-stroke (a theme color such as red.dark) to follow light and dark themes."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon={Upload}
            loading={busy}
            disabled={!svg || !title.trim() || !concept.trim()}
            onClick={() => void submit()}
          >
            Import
          </Button>
        </>
      }
    >
      <div className="import-form">
        <label className="ui-file">
          <input type="file" accept=".svg,image/svg+xml" onChange={(e) => void pick(e)} />
          <span>{file || "Choose an SVG file"}</span>
        </label>
        <TextField label="Title" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
        <TextField
          label="What it is"
          hint="e.g. traffic light"
          value={concept}
          maxLength={80}
          onChange={(e) => setConcept(e.target.value)}
        />
        <TextField
          label="Names (comma separated)"
          hint="Empty: what it is"
          value={aliases}
          onChange={(e) => setAliases(e.target.value)}
        />
        <TextArea
          label="What the drawing shows"
          rows={2}
          maxLength={300}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        {error && <Alert tone="danger">{error}</Alert>}
      </div>
    </Modal>
  );
}

function TryScene({ onClose, onOpen }: { onClose: () => void; onOpen: (id: string) => void }) {
  const [brief, setBrief] = useState("heart: the human heart, big in the centre\nThe heart pumps blood to the lungs.");
  const [lesson, setLesson] = useState("How the heart works");
  const [got, setGot] = useState<ObjectMatch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setGot(await v1.matchObjects(brief, lesson));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      title="Try a scene"
      description="Which drawings a cost-saver scene with this brief would be offered. Lines like 'id: description' are the scene's objects; the rest is what it says and shows."
      size="lg"
      footer={
        <Button variant="primary" icon={Search} loading={busy} onClick={() => void run()}>
          Find drawings
        </Button>
      }
    >
      <div className="import-form">
        <TextArea
          label="Scene brief"
          rows={6}
          maxLength={6000}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
        />
        <TextField label="Lesson title" maxLength={300} value={lesson} onChange={(e) => setLesson(e.target.value)} />
        {error && <Alert tone="danger">{error}</Alert>}
        {got && (
          <div className="match-results">
            <h3>Offered to the scene</h3>
            {got.offered.length === 0 ? (
              <p className="muted small">None: the scene would draw everything itself.</p>
            ) : (
              <ul className="plain-list">
                {got.offered.map((m) => (
                  <li key={m.id}>
                    <button type="button" className="link-button" onClick={() => onOpen(m.id)}>
                      {m.title}
                    </button>{" "}
                    <small className="muted">
                      {m.id}
                      {m.for.length ? ` · for ${m.for.join(", ")}` : ""}
                    </small>
                  </li>
                ))}
              </ul>
            )}
            {got.candidates.length > 0 && (
              <>
                <h3>Would also match, once approved</h3>
                <ul className="plain-list">
                  {got.candidates.map((m) => (
                    <li key={m.id}>
                      <button type="button" className="link-button" onClick={() => onOpen(m.id)}>
                        {m.title}
                      </button>{" "}
                      <small className="muted">{m.id}</small>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
