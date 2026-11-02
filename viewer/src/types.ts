export interface Evidence {
  t: number; t_end?: number; evidence_id?: string; source_type?: string;
  label: string; excerpt?: string; frame_url?: string;
}
export interface Answer {
  context?:{through_s:number;story_through_s:number;profile_version:number;selected_records:number;semantic_search:boolean;
    creator_profile?:{channel:string;streamer:string;character:string;activity:string};
    recording_context?:{title:string;description:string;channel:string;activity:string;basis:string};
    frames?:{evidence_id:string;time_s:number}[];
    records:{evidence_id:string;start_s:number;end_s:number;source_type:string;role:string;excerpt:string}[]};
  coverage_gaps?: CoverageGap[];
  summary: string; query_type?: string; citations: Evidence[];
  clips?: { t_start: number; t_end: number; label: string }[];
  tool_trace?: unknown[]; source_label?: string; status?: string;
  run_id?: string; through_s?: number;
}
export interface MemoryRecord {
  evidence_id?: string; t_start: number; t_end: number; modality: string;
  kind?: string; text?: string; caption?: string; activity?: string;
  entities?: string[]; speaker?: string; payload?: Record<string, unknown>;
}
export interface Frame { id: string; t: number; url: string }
export interface Memory {
  coverage_gaps?: CoverageGap[];
  records: MemoryRecord[]; frames: Frame[]; run_id?: string; through_s?: number;
  server?: { has_video?: boolean; video_url?: string };
  video?: { title?: string; duration_s?: number };
}
export interface Status {
  coverage_gaps?: CoverageGap[]; resume_available?:boolean;
  audio_queue_depth?:number; vision_queue_depth?:number;
  run_id: string; mode: string; title: string; duration_s: number;
  has_video: boolean; playback_s: number; processed_through_s: number;
  audio_through_s: number; vision_through_s: number; state: string;
  gap?: unknown; error?: string | null; provider?: string; csrf_token: string;
  retry_available?: boolean;
  pipeline?: {frame_interval_s:number;frame_width:number;answer_style:string;allow_predictions:boolean;catch_up?:string};
  twitch?: { state?: string };
  source_connected?: boolean; source_age_s?: number | null;
  queue_depth?: number; questions_waiting?: number;
  active_job?: { kind: string; age_s: number } | null;
  active_jobs?: { kind: string; age_s: number }[];
  metrics?: { last_vision_s?: number | null; last_audio_s?: number | null; last_answer_s?: number | null };
}
export interface CoverageGap {start_s:number;end_s:number;lane:string;reason:string}
export interface Shared {
  run_id: string; rotation: number; selection: number; answer: Answer | null;
  controls_enabled: boolean; revision: number;
  source_command?: 'play' | 'pause' | 'restart' | null; source_revision?: number;
}
