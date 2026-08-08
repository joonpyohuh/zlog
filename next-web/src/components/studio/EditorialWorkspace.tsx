"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type OutputType = "long" | "short";

type Scene = {
  scene_id: string;
  event_id: string;
  label: string;
  roles: string[];
  source_range: { start_ms: number; end_ms: number };
  technical_quality: string;
};

type TripGraph = {
  title: string;
  input_mode: "multiple_clips" | "single_long_video";
  days: { day_id: string; title: string; event_ids: string[] }[];
  events: { event_id: string; title: string; scene_ids: string[] }[];
  scenes: Scene[];
};

type VideoItem = {
  item_id: string;
  scene_id: string;
  segment_id: string;
  source_file: string;
  source_in_ms: number;
  source_out_ms: number;
  timeline_start_frame: number;
  duration_frames: number;
  roles: string[];
  decision_reason: string;
  focus_x: number;
  focus_y: number;
  crop_confidence: number;
  effect: {
    effect_id: string;
    start_frame: number;
    duration_frames: number;
    reason: string;
  };
};

type Timeline = {
  timeline_id: string;
  project: string;
  output_type: OutputType;
  revision: number;
  fps: number;
  width: number;
  height: number;
  target_duration_sec: number;
  duration_frames: number;
  creative_direction: string;
  hypothesis_version: string;
  video_items: VideoItem[];
  captions: {
    item_id: string;
    start_frame: number;
    duration_frames: number;
    text: string;
    position: string;
    style: string;
    animation: string;
    grounding: string;
  }[];
  music: {
    music_id: string;
    start_frame: number;
    duration_frames: number;
    volume: number;
    selected_by_user: boolean;
  } | null;
};

type Critic = {
  passed: boolean;
  issues: { code: string; message: string }[];
};

type EditorPayload = {
  trip_graph: TripGraph;
  long_timeline: Timeline;
  short_timeline: Timeline;
  long_critic: Critic;
  short_critic: Critic;
};

type MusicTrack = {
  music_id: string;
  title: string;
  artist: string;
  mood_tags: string[];
  energy: string;
  preview_url: string;
};

function reflow(
  timeline: Timeline,
  items: VideoItem[],
  scenes: Map<string, Scene>,
): Timeline {
  let cursor = 0;
  const videoItems = items.map((item) => {
    const scene = scenes.get(item.scene_id);
    const availableMs =
      (scene?.source_range.end_ms ?? item.source_out_ms) - item.source_in_ms;
    const durationFrames = Math.max(
      1,
      Math.min(
        item.duration_frames,
        Math.floor((availableMs / 1000) * timeline.fps),
      ),
    );
    const next = {
      ...item,
      timeline_start_frame: cursor,
      duration_frames: durationFrames,
      source_out_ms:
        item.source_in_ms + Math.round((durationFrames / timeline.fps) * 1000),
    };
    cursor += durationFrames;
    return next;
  });
  return {
    ...timeline,
    duration_frames: cursor,
    video_items: videoItems,
    captions: timeline.captions.filter(
      (caption) => caption.start_frame + caption.duration_frames <= cursor,
    ),
    music: timeline.music
      ? { ...timeline.music, duration_frames: cursor }
      : null,
  };
}

export function EditorialWorkspace({
  apiBase,
  jobId,
  onRenderQueued,
}: {
  apiBase: string;
  jobId: string;
  onRenderQueued: (output: OutputType) => void;
}) {
  const base = apiBase.replace(/\/$/, "");
  const apiUrl = useCallback(
    (path: string) =>
      /^https?:\/\//i.test(path)
        ? path
        : `${base}${path.startsWith("/") ? path : `/${path}`}`,
    [base],
  );
  const [editor, setEditor] = useState<EditorPayload | null>(null);
  const [output, setOutput] = useState<OutputType>("short");
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [tracks, setTracks] = useState<MusicTrack[]>([]);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    void Promise.all([
      fetch(apiUrl(`/api/jobs/${jobId}/editor`)).then((response) => {
        if (!response.ok) throw new Error("편집 데이터를 불러오지 못했습니다.");
        return response.json() as Promise<EditorPayload>;
      }),
      fetch(apiUrl(`/api/jobs/${jobId}/music-recommendations`)).then(
        (response) => response.json() as Promise<{ tracks: MusicTrack[] }>,
      ),
    ])
      .then(([data, music]) => {
        setEditor(data);
        setTimeline(data.short_timeline);
        setTracks(music.tracks);
      })
      .catch((error: unknown) =>
        setMessage(
          error instanceof Error
            ? error.message
            : "편집 데이터를 불러오지 못했습니다.",
        ),
      );
  }, [apiUrl, jobId]);

  const scenes = useMemo(
    () =>
      new Map(
        editor?.trip_graph.scenes.map((scene) => [scene.scene_id, scene]) ?? [],
      ),
    [editor],
  );
  const critic = editor?.[output === "long" ? "long_critic" : "short_critic"];

  function switchOutput(next: OutputType) {
    if (!editor) return;
    setOutput(next);
    setTimeline(editor[next === "long" ? "long_timeline" : "short_timeline"]);
    setMessage("");
  }

  function mutateItems(items: VideoItem[]) {
    if (timeline) setTimeline(reflow(timeline, items, scenes));
  }

  async function save(): Promise<boolean> {
    if (!timeline) return false;
    setMessage("저장 중…");
    const response = await fetch(
      apiUrl(`/api/jobs/${jobId}/timelines/${output}`),
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timeline }),
      },
    );
    if (!response.ok) {
      setMessage(
        response.status === 409
          ? "다른 변경이 먼저 저장됐습니다. 다시 불러와 주세요."
          : "저장하지 못했습니다.",
      );
      return false;
    }
    const data = (await response.json()) as {
      timeline: Timeline;
      critic: Critic;
    };
    setTimeline(data.timeline);
    setEditor((current) =>
      current
        ? {
            ...current,
            [output === "long" ? "long_timeline" : "short_timeline"]:
              data.timeline,
            [output === "long" ? "long_critic" : "short_critic"]: data.critic,
          }
        : current,
    );
    setMessage("저장했습니다.");
    return true;
  }

  async function render() {
    if (!(await save())) return;
    const response = await fetch(
      apiUrl(`/api/jobs/${jobId}/render/${output}`),
      { method: "POST" },
    );
    if (!response.ok) {
      setMessage("렌더링을 시작하지 못했습니다.");
      return;
    }
    onRenderQueued(output);
  }

  async function search() {
    const response = await fetch(
      apiUrl(`/api/music/search?q=${encodeURIComponent(query)}`),
    );
    const data = (await response.json()) as { tracks: MusicTrack[] };
    setTracks(data.tracks);
  }

  async function selectMusic(musicId: string | null) {
    if (!timeline) return;
    const response = await fetch(apiUrl(`/api/jobs/${jobId}/music`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        output_type: output,
        music_id: musicId,
        revision: timeline.revision,
      }),
    });
    if (!response.ok) {
      setMessage("음악을 선택하지 못했습니다.");
      return;
    }
    const data = (await response.json()) as { timeline: Timeline };
    setTimeline(data.timeline);
    setEditor((current) =>
      current
        ? {
            ...current,
            [output === "long" ? "long_timeline" : "short_timeline"]:
              data.timeline,
          }
        : current,
    );
    setMessage(
      musicId ? "음악을 선택했습니다." : "음악 없이 현장음을 사용합니다.",
    );
  }

  if (!editor || !timeline) {
    return (
      <p className="py-8 text-sm text-neutral-500" aria-live="polite">
        {message || "여행 구조와 편집 결정을 불러오는 중…"}
      </p>
    );
  }

  return (
    <section
      className="space-y-8 border-t border-neutral-800 pt-8"
      aria-label="AI 편집 워크스페이스"
    >
      <div>
        <p className="text-xs uppercase tracking-[0.2em] text-neutral-500">
          Trip structure · {editor.trip_graph.input_mode.replaceAll("_", " ")}
        </p>
        <h2 className="mt-2 text-2xl font-medium text-white">
          {editor.trip_graph.title}
        </h2>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {editor.trip_graph.days.map((day) => (
            <article
              key={day.day_id}
              className="rounded-2xl border border-neutral-800 bg-neutral-950 p-4"
            >
              <h3 className="font-medium text-white">{day.title}</h3>
              <ol className="mt-3 space-y-2 text-sm text-neutral-400">
                {day.event_ids.map((eventId) => {
                  const event = editor.trip_graph.events.find(
                    (item) => item.event_id === eventId,
                  );
                  return (
                    <li key={eventId}>
                      {event?.title ?? "Unknown event"}{" "}
                      <span className="text-neutral-600">
                        · {event?.scene_ids.length ?? 0} scenes
                      </span>
                    </li>
                  );
                })}
              </ol>
            </article>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          className="flex rounded-xl border border-neutral-800 p-1"
          role="tablist"
          aria-label="출력 형식"
        >
          {(["short", "long"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              role="tab"
              aria-selected={output === kind}
              onClick={() => switchOutput(kind)}
              className={`rounded-lg px-4 py-2 text-sm ${output === kind ? "bg-white text-black" : "text-neutral-400"}`}
            >
              {kind === "short" ? "Short · 30–40 sec" : "Long · up to 10 min"}
            </button>
          ))}
        </div>
        <p className="text-sm text-neutral-500">
          {(timeline.duration_frames / timeline.fps).toFixed(1)} sec · revision{" "}
          {timeline.revision}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="space-y-2">
          {timeline.video_items.map((item, index) => {
            const scene = scenes.get(item.scene_id);
            const seconds = item.duration_frames / timeline.fps;
            return (
              <article
                key={item.item_id}
                className="grid gap-3 rounded-xl border border-neutral-800 bg-black/50 p-4 sm:grid-cols-[2rem_minmax(0,1fr)_7rem_auto] sm:items-center"
              >
                <span className="text-sm tabular-nums text-neutral-600">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div>
                  <h3 className="text-sm font-medium text-white">
                    {scene?.label ?? item.scene_id}
                  </h3>
                  <p className="mt-1 line-clamp-2 text-xs text-neutral-500">
                    {item.decision_reason}
                  </p>
                  <p className="mt-2 text-[11px] uppercase tracking-wide text-neutral-600">
                    {item.roles.join(" · ")} · {item.effect.effect_id}
                  </p>
                </div>
                <label className="text-xs text-neutral-500">
                  Duration
                  <input
                    type="number"
                    min="0.4"
                    max={
                      ((scene?.source_range.end_ms ?? item.source_out_ms) -
                        item.source_in_ms) /
                      1000
                    }
                    step="0.1"
                    value={seconds.toFixed(1)}
                    onChange={(event) => {
                      const frames = Math.max(
                        1,
                        Math.round(Number(event.target.value) * timeline.fps),
                      );
                      mutateItems(
                        timeline.video_items.map((candidate) =>
                          candidate.item_id === item.item_id
                            ? { ...candidate, duration_frames: frames }
                            : candidate,
                        ),
                      );
                    }}
                    className="mt-1 w-full rounded-lg border border-neutral-800 bg-neutral-950 px-2 py-2 text-white"
                  />
                </label>
                <div className="flex gap-1">
                  <button
                    type="button"
                    aria-label="앞으로 이동"
                    disabled={index === 0}
                    onClick={() => {
                      const next = [...timeline.video_items];
                      [next[index - 1], next[index]] = [
                        next[index],
                        next[index - 1],
                      ];
                      mutateItems(next);
                    }}
                    className="rounded border border-neutral-800 px-2 py-1 text-neutral-400 disabled:opacity-30"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label="뒤로 이동"
                    disabled={index === timeline.video_items.length - 1}
                    onClick={() => {
                      const next = [...timeline.video_items];
                      [next[index], next[index + 1]] = [
                        next[index + 1],
                        next[index],
                      ];
                      mutateItems(next);
                    }}
                    className="rounded border border-neutral-800 px-2 py-1 text-neutral-400 disabled:opacity-30"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label="컷 제거"
                    disabled={timeline.video_items.length === 1}
                    onClick={() =>
                      mutateItems(
                        timeline.video_items.filter(
                          (candidate) => candidate.item_id !== item.item_id,
                        ),
                      )
                    }
                    className="rounded border border-neutral-800 px-2 py-1 text-neutral-400 disabled:opacity-30"
                  >
                    ×
                  </button>
                </div>
              </article>
            );
          })}
        </div>

        <aside className="space-y-5">
          <div className="rounded-2xl border border-neutral-800 p-4">
            <h3 className="text-sm font-medium text-white">Editorial critic</h3>
            {critic?.passed ? (
              <p className="mt-3 text-sm text-emerald-400">
                구조적 충돌이 없습니다.
              </p>
            ) : (
              <ul className="mt-3 space-y-3 text-xs text-neutral-400">
                {critic?.issues.map((issue, index) => (
                  <li key={`${issue.code}-${index}`}>
                    <span className="text-amber-300">{issue.code}</span>
                    <br />
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-2xl border border-neutral-800 p-4">
            <h3 className="text-sm font-medium text-white">Music</h3>
            <p className="mt-1 text-xs text-neutral-500">
              추천만 제공합니다. 선택 전에는 자동 삽입하지 않습니다.
            </p>
            <form
              className="mt-3 flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void search();
              }}
            >
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search music"
                className="min-w-0 flex-1 rounded-lg border border-neutral-800 bg-black px-2 py-2 text-xs text-white"
              />
              <button className="rounded-lg border border-neutral-700 px-2 text-xs text-neutral-300">
                Search
              </button>
            </form>
            <div className="mt-3 space-y-3">
              {tracks.map((track) => (
                <div
                  key={track.music_id}
                  className="border-t border-neutral-900 pt-3"
                >
                  <p className="text-sm text-white">{track.title}</p>
                  <p className="text-xs text-neutral-600">
                    {track.artist} · {track.energy}
                  </p>
                  <audio
                    className="mt-2 h-8 w-full"
                    src={apiUrl(track.preview_url)}
                    controls
                    preload="none"
                  />
                  <button
                    type="button"
                    onClick={() => void selectMusic(track.music_id)}
                    className="mt-2 text-xs text-neutral-300 underline"
                  >
                    {timeline.music?.music_id === track.music_id
                      ? "Selected"
                      : "Use this music"}
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => void selectMusic(null)}
              className="mt-4 text-xs text-neutral-500 underline"
            >
              No music · keep location sound
            </button>
          </div>
        </aside>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          className="rounded-lg border border-neutral-700 px-4 py-2 text-sm text-white"
        >
          Save edit
        </button>
        <button
          type="button"
          onClick={() => void render()}
          className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black"
        >
          Render {output}
        </button>
        <p className="text-sm text-neutral-500" aria-live="polite">
          {message}
        </p>
      </div>
    </section>
  );
}
