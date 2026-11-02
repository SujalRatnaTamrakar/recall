import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { Evidence, Frame, MemoryRecord } from './types';

const palette = [0xb8f76c, 0x9e8cff, 0x66cce2, 0xf8b86a, 0xec88b5];
const secondsPerUnit = 15;
const overviewLimit = 32;
const topicNodeLimit = 64;

/** A video volume: XY is the image, Z is time. Ribbons are semantic lanes. */
export class TimeCube {
  private renderer!: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(37, 1, 0.1, 100);
  private controls!: OrbitControls;
  private volume = new THREE.Group();
  private content = new THREE.Group();
  private results = new THREE.Group();
  private cursor!: THREE.LineSegments;
  private outline!: THREE.LineSegments;
  private grid!: THREE.GridHelper;
  private currentPlane!: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  private previewVideo = document.createElement('video');
  private previewCanvas = document.createElement('canvas');
  private previewTexture = new THREE.CanvasTexture(this.previewCanvas);
  private previewSource = '';
  private previewRun = '';
  private previewCutoff = 0;
  private previewTarget = 0;
  private previewReady = false;
  private faceTop = document.createElement('canvas');
  private faceSide = document.createElement('canvas');
  private faceTopTexture = new THREE.CanvasTexture(this.faceTop);
  private faceSideTexture = new THREE.CanvasTexture(this.faceSide);
  private frameReadout!: HTMLDivElement;
  private loader = new THREE.TextureLoader();
  private textures = new Map<string, THREE.Texture>();
  private snapshots: Frame[] = [];
  private records: MemoryRecord[] = [];
  private requestedTime = 0;
  private selectable: THREE.Object3D[] = [];
  private duration = 0;
  private depth = 0.4;
  private autoFit = true;
  private highlighted: Evidence[] = [];
  private highlightedSelection = 0;
  private signature = '';
  private reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  private valid = false;
  private needsRender = true;
  private dragStart = { x: 0, y: 0 };
  private topicLabels: string[] = [];

  get topics() { return this.topicLabels; }

  constructor(private host: HTMLElement, private onPick: (t: number) => void) {
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
      this.renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
      this.renderer.setClearColor(0x0a0d13, 0);
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.renderer.domElement.setAttribute('aria-label', 'Growing three-dimensional video memory. Drag to orbit, scroll to zoom, right-drag to pan. Use the timeline below for keyboard navigation.');
      this.host.append(this.renderer.domElement);
      this.frameReadout = document.createElement('div');
      this.frameReadout.setAttribute('role', 'status');
      this.frameReadout.setAttribute('aria-live', 'polite');
      Object.assign(this.frameReadout.style, {
        position: 'absolute', top: '47px', right: '20px', zIndex: '3', maxWidth: '70%',
        color: '#d7e9cf', background: 'rgba(12, 19, 20, .88)', border: '1px solid #415344',
        borderRadius: '5px', padding: '6px 8px', fontSize: '14px', lineHeight: '1.35',
        pointerEvents: 'none', letterSpacing: '.01em',
      });
      this.frameReadout.textContent = 'Waiting for a sampled frame';
      this.host.append(this.frameReadout);
      this.camera.position.set(10.4, 6.9, 13.8);
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = !this.reduced;
      this.controls.dampingFactor = 0.07;
      this.controls.minDistance = 7;
      this.controls.maxDistance = 34;
      this.controls.enablePan = true;
      this.controls.addEventListener('start', () => { this.autoFit = false; });
      this.controls.maxPolarAngle = Math.PI * 0.8;
      this.scene.add(this.volume);
      this.volume.add(this.content, this.results);
      this.grid = new THREE.GridHelper(24, 24, 0x263340, 0x18202b);
      this.grid.position.y = -2.5;
      (this.grid.material as THREE.Material).transparent = true;
      (this.grid.material as THREE.Material).opacity = 0.45;
      this.scene.add(this.grid);
      this.outline = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(6.4, 3.6, 1)),
        new THREE.LineBasicMaterial({ color: 0x62717e, transparent: true, opacity: 0.5 }),
      );
      this.outline.scale.z = this.depth;
      this.volume.add(this.outline);
      this.cursor = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.PlaneGeometry(6.55, 3.7)),
        new THREE.LineBasicMaterial({ color: 0xd7ffae, transparent: true, opacity: 0.9 }),
      );
      this.volume.add(this.cursor);
      this.currentPlane = new THREE.Mesh(new THREE.PlaneGeometry(6.4, 3.6),
        new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, transparent: true, opacity: 0.86, depthWrite: false }));
      this.currentPlane.visible = false;
      this.currentPlane.renderOrder = 2;
      this.volume.add(this.currentPlane);
      // Decode the requested image from the source video itself. This is a
      // separate, muted player: scrubbing must never seek the broadcast player.
      this.previewVideo.muted = true;
      this.previewVideo.playsInline = true;
      this.previewVideo.preload = 'metadata';
      this.previewVideo.addEventListener('loadedmetadata', () => this.seekPreview());
      this.previewVideo.addEventListener('seeked', () => this.drawPreview());
      this.previewVideo.addEventListener('loadeddata', () => this.drawPreview());
      this.previewTexture.colorSpace = THREE.SRGBColorSpace;
      this.faceTopTexture.colorSpace = THREE.SRGBColorSpace;
      this.faceSideTexture.colorSpace = THREE.SRGBColorSpace;
      this.renderer.domElement.addEventListener('pointerdown', e => { this.dragStart = { x: e.clientX, y: e.clientY }; });
      this.renderer.domElement.addEventListener('pointerup', e => {
        if (Math.hypot(e.clientX - this.dragStart.x, e.clientY - this.dragStart.y) > 5) return;
        const bounds = this.renderer.domElement.getBoundingClientRect();
        const pointer = new THREE.Vector2((e.clientX - bounds.left) / bounds.width * 2 - 1, -(e.clientY - bounds.top) / bounds.height * 2 + 1);
        const ray = new THREE.Raycaster();
        ray.setFromCamera(pointer, this.camera);
        const hit = ray.intersectObjects(this.selectable, false)[0];
        if (hit && typeof hit.object.userData.t === 'number') this.onPick(hit.object.userData.t);
      });
      new ResizeObserver(() => this.resize()).observe(host);
      this.valid = true;
      this.resize();
      this.renderer.setAnimationLoop(() => {
        if (document.hidden || !this.host.getClientRects().length) return;
        if (this.controls.update() || this.needsRender) {
          this.renderer.render(this.scene, this.camera);
          this.needsRender = false;
        }
      });
    } catch {
      host.innerHTML = '<div class="webgl-fallback"><strong>Your memory, on a timeline.</strong><span>The 3D view needs WebGL. Search and the keyboard timeline below still work.</span></div>';
    }
  }

  private resize() {
    if (!this.valid) return;
    const width = this.host.clientWidth;
    const height = this.host.clientHeight;
    this.renderer.setSize(width, height);
    this.camera.aspect = width / Math.max(height, 1);
    this.camera.updateProjectionMatrix();
    this.needsRender = true;
    if (this.autoFit) this.fitVolume();
  }

  // Fixed time scale: older frames never compress as more memory arrives.
  // Recentring the whole volume preserves orbit controls around its midpoint.
  private z(t: number) { return this.depth / 2 - Math.min(this.duration, Math.max(0, t)) / secondsPerUnit; }

  private fitVolume() {
    const vertical = THREE.MathUtils.degToRad(this.camera.fov / 2);
    const horizontal = Math.atan(Math.tan(vertical) * this.camera.aspect);
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    if (!direction.lengthSq()) direction.set(10.4, 6.9, 13.8).normalize();
    const right = new THREE.Vector3().crossVectors(this.camera.up, direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction, right).normalize();
    let distance = 14;
    // Fit the projected corners, rather than a sphere around the long volume.
    // The latter made long recordings unnecessarily tiny on a wide canvas.
    for (const x of [-3.3, 3.3]) for (const y of [-2.3, 1.9]) for (const z of [-this.depth / 2, this.depth / 2]) {
      const corner = new THREE.Vector3(x, y, z);
      distance = Math.max(distance, corner.dot(direction) + Math.max(
        Math.abs(corner.dot(right)) / Math.tan(horizontal), Math.abs(corner.dot(up)) / Math.tan(vertical)));
    }
    distance *= 1.12;
    this.controls.target.set(0, 0, 0);
    this.camera.position.copy(direction.multiplyScalar(distance));
    this.camera.far = Math.max(100, distance * 4 + this.depth * 2);
    this.controls.maxDistance = Math.max(34, distance * 3);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.needsRender = true;
  }

  private growVolume(duration: number) {
    this.duration = Math.max(0, Number.isFinite(duration) ? duration : 0);
    this.depth = Math.max(0.4, this.duration / secondsPerUnit);
    this.outline.scale.z = this.depth;
    this.grid.scale.z = Math.max(1, (this.depth + 8) / 24);
    this.host.dataset.memorySeconds = String(this.duration);
    this.host.dataset.volumeDepth = String(this.depth);
    this.host.dataset.secondsPerUnit = String(secondsPerUnit);
    this.renderer.domElement.setAttribute('aria-label', `Growing video memory, ${this.timeLabel(this.duration)} processed. Time extends along the volume at a fixed scale. Drag to orbit, scroll to zoom, right-drag to pan. Reset view fits the entire memory.`);
    // Keep long volumes reachable even when the viewer has chosen a manual view.
    this.camera.far = Math.max(this.camera.far, this.depth * 8 + 100);
    this.controls.maxDistance = Math.max(this.controls.maxDistance, this.depth * 6 + 34);
    this.camera.updateProjectionMatrix();
    if (this.autoFit) this.fitVolume();
    this.cursor.position.z = this.z(this.requestedTime);
    this.needsRender = true;
  }

  private timeLabel(time: number) {
    const seconds = Math.max(0, Math.floor(time));
    const hours = Math.floor(seconds / 3600);
    return `${hours ? `${hours}:` : ''}${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }

  private showTimeSlice() {
    // A source sample must have happened by the inspected time. Do not show a
    // nearest future frame merely because it is visually closer on the timeline.
    const frame = [...this.snapshots].reverse().find(candidate => candidate.t <= Math.min(this.requestedTime, this.previewCutoff));
    const texture = frame ? this.textures.get(frame.url) : undefined;
    const accuratePreview = this.previewReady && this.previewSource && Math.abs(this.previewVideo.currentTime - this.previewTarget) < .1 && this.previewVideo.currentTime <= this.previewCutoff + .001;
    const selectedTexture = accuratePreview ? this.previewTexture : texture;
    this.currentPlane.visible = Boolean(selectedTexture);
    if (this.currentPlane.material.map !== (selectedTexture || null)) {
      this.currentPlane.material.map = selectedTexture || null;
      this.currentPlane.material.needsUpdate = true;
    }
    let readout = 'No sampled frame at this time';
    if (accuratePreview) {
      this.currentPlane.position.z = this.z(this.previewVideo.currentTime);
      readout = `Video frame ${this.timeLabel(this.previewVideo.currentTime)} · drag timeline to scrub`;
      this.frameReadout.dataset.frameSource = 'video';
      this.frameReadout.dataset.frameTime = String(this.previewVideo.currentTime);
    } else if (frame && texture) {
      this.currentPlane.position.z = this.z(frame.t);
      readout = `Frame ${this.timeLabel(frame.t)} · latest sample at or before cursor`;
      this.frameReadout.dataset.frameSource = 'sample';
      this.frameReadout.dataset.frameTime = String(frame.t);
    } else {
      delete this.frameReadout.dataset.frameSource;
      delete this.frameReadout.dataset.frameTime;
    }
    // Avoid repeatedly announcing the same frame on every status poll.
    readout += ` · ${this.timeLabel(this.duration)} memory`;
    if (this.frameReadout.textContent !== readout) this.frameReadout.textContent = readout;
    this.needsRender = true;
  }

  /** Independent, bounded playback used solely for the inspected image. */
  setVideoSource(url: string | null, runId: string, cutoff: number) {
    if (!this.valid) return;
    let source = '';
    if (url) {
      try {
        const parsed = new URL(url, location.origin);
        if (parsed.origin === location.origin && parsed.pathname === '/video') source = parsed.href;
      } catch { /* Ignore untrusted URLs. */ }
    }
    const changed = source !== this.previewSource || runId !== this.previewRun;
    const previousCutoff = this.previewCutoff;
    this.previewCutoff = Math.max(0, Number.isFinite(cutoff) ? cutoff : 0);
    if (changed) {
      // A new run must never display frames or the selected image from the old run.
      if (runId !== this.previewRun) {
        this.snapshots = [];
        this.records = [];
        this.topicLabels = [];
        this.clear(this.content);
        this.clear(this.results);
        for (const texture of this.textures.values()) texture.dispose();
        this.textures.clear();
        this.selectable = [];
        this.highlighted = [];
        this.signature = '';
        this.requestedTime = 0;
        this.autoFit = true;
        this.camera.position.set(10.4, 6.9, 13.8);
        this.controls.target.set(0, 0, 0);
        this.volume.rotation.y = 0;
        this.growVolume(0);
        this.host.dataset.overviewFrames = '0';
      }
      this.previewSource = source;
      this.previewRun = runId;
      this.previewReady = false;
      this.previewVideo.removeAttribute('src');
      this.previewVideo.load();
      if (source) {
        this.previewVideo.src = source;
        this.previewVideo.load();
      }
    }
    if (this.previewCutoff < this.duration) this.update(this.snapshots, this.records, this.previewCutoff);
    if (this.previewCutoff !== previousCutoff) this.drawSampledFaces();
    this.seekPreview();
  }

  private seekPreview() {
    this.previewTarget = Math.max(0, Math.min(this.requestedTime, this.previewCutoff));
    if (!this.previewSource || this.previewVideo.readyState < 1) { this.showTimeSlice(); return; }
    // The requested time can be ahead of what has been processed. Always
    // clear the previous image before seeking so future content cannot linger.
    if (Math.abs(this.previewVideo.currentTime - this.previewTarget) > .008) {
      this.previewReady = false;
      try { this.previewVideo.currentTime = this.previewTarget; } catch { /* Metadata/decoder not ready yet. */ }
    } else if (!this.previewReady && this.previewVideo.readyState >= 2 && !this.previewVideo.seeking) {
      this.drawPreview();
    }
    this.showTimeSlice();
  }

  private drawPreview() {
    const video = this.previewVideo;
    if (!this.previewSource || video.seeking || video.readyState < 2 || !video.videoWidth) return;
    if (video.currentTime > this.previewCutoff + .001 || Math.abs(video.currentTime - this.previewTarget) >= .1) {
      this.previewReady = false;
      this.seekPreview();
      return;
    }
    this.previewCanvas.width = Math.min(1280, video.videoWidth);
    this.previewCanvas.height = Math.max(1, Math.round(this.previewCanvas.width * video.videoHeight / video.videoWidth));
    this.previewCanvas.getContext('2d')?.drawImage(video, 0, 0, this.previewCanvas.width, this.previewCanvas.height);
    this.previewTexture.needsUpdate = true;
    this.previewReady = true;
    this.showTimeSlice();
  }

  private drawSampledFaces() {
    // The faces visualise centre-row/column samples over time. Intervals are
    // filled from the previous sample, not represented as decoded every-frame
    // footage. The movable inspection plane is decoded independently above.
    const top = this.faceTop.getContext('2d');
    const side = this.faceSide.getContext('2d');
    if (!top || !side) return;
    top.clearRect(0, 0, this.faceTop.width, this.faceTop.height);
    side.clearRect(0, 0, this.faceSide.width, this.faceSide.height);
    for (let i = 0; i < this.snapshots.length; i++) {
      const frame = this.snapshots[i];
      const image = this.textures.get(frame.url)?.image as HTMLImageElement | undefined;
      if (!image?.complete || !image.naturalWidth || frame.t > this.previewCutoff) continue;
      const end = Math.min(this.previewCutoff, this.snapshots[i + 1]?.t ?? this.previewCutoff);
      const from = Math.max(0, Math.min(1, frame.t / (this.depth * secondsPerUnit)));
      const to = Math.max(from, Math.min(1, end / (this.depth * secondsPerUnit)));
      const topY = Math.floor(from * this.faceTop.height);
      const topHeight = Math.max(1, Math.ceil(to * this.faceTop.height) - topY);
      const sideX = Math.floor(from * this.faceSide.width);
      const sideWidth = Math.max(1, Math.ceil(to * this.faceSide.width) - sideX);
      top.drawImage(image, 0, Math.floor(image.naturalHeight / 2), image.naturalWidth, 1, 0, topY, this.faceTop.width, topHeight);
      side.drawImage(image, Math.floor(image.naturalWidth / 2), 0, 1, image.naturalHeight, sideX, 0, sideWidth, this.faceSide.height);
    }
    this.faceTopTexture.needsUpdate = true;
    this.faceSideTexture.needsUpdate = true;
    this.needsRender = true;
  }

  private clear(group: THREE.Group) {
    for (const child of [...group.children]) {
      group.remove(child);
      child.traverse(object => {
        const mesh = object as THREE.Mesh;
        mesh.geometry?.dispose();
        if (mesh.material) for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material.dispose();
      });
    }
  }

  update(frames: Frame[], records: MemoryRecord[], duration: number) {
    if (!this.valid) return;
    const elapsed = Math.max(0, Math.min(Number.isFinite(duration) ? duration : 0, this.previewCutoff));
    // Only request same-origin evidence-frame URLs supplied by the bounded API.
    const safeFrames = frames.filter(frame => {
      if (!Number.isFinite(frame.t) || frame.t < 0 || frame.t > elapsed) return false;
      try {
        const url = new URL(frame.url, location.origin);
        return url.origin === location.origin && (url.pathname.startsWith('/frames/') || url.pathname.startsWith('/api/frames/'));
      } catch { return false; }
    }).sort((a, b) => a.t - b.t);
    const sampled = safeFrames.length <= overviewLimit ? safeFrames : Array.from({ length: overviewLimit }, (_, i) => safeFrames[Math.round(i * (safeFrames.length - 1) / (overviewLimit - 1))]);
    const boundedRecords = records.filter(record => Number.isFinite(record.t_start) && record.t_start >= 0 && Number.isFinite(record.t_end) && record.t_end <= elapsed && record.t_start <= record.t_end &&
      (record.payload?.window_end === undefined || (typeof record.payload.window_end === 'number' && Number.isFinite(record.payload.window_end) && record.payload.window_end <= elapsed)));
    const signature = sampled.map(f => `${f.id}:${f.t}:${f.url}`).join('|') + `/${elapsed}/${boundedRecords.length}`;
    if (signature === this.signature) return;
    this.signature = signature;
    this.growVolume(elapsed);
    this.clear(this.content);
    this.selectable = [];
    this.snapshots = sampled;
    this.records = boundedRecords;
    this.host.dataset.overviewFrames = String(sampled.length);
    this.faceTop.width = 512; this.faceTop.height = 512;
    this.faceSide.width = 512; this.faceSide.height = 288;
    const topFace = new THREE.Mesh(new THREE.PlaneGeometry(6.4, this.depth), new THREE.MeshBasicMaterial({ map: this.faceTopTexture, side: THREE.DoubleSide, transparent: true, opacity: .95, depthWrite: false }));
    topFace.rotation.x = Math.PI / 2;
    topFace.position.y = 1.81;
    this.content.add(topFace);
    const sideFace = new THREE.Mesh(new THREE.PlaneGeometry(this.depth, 3.6), new THREE.MeshBasicMaterial({ map: this.faceSideTexture, side: THREE.DoubleSide, transparent: true, opacity: .95, depthWrite: false }));
    sideFace.rotation.y = Math.PI / 2;
    sideFace.position.x = 3.21;
    this.content.add(sideFace);
    for (const frame of sampled) {
      let texture = this.textures.get(frame.url);
      if (!texture) {
        texture = this.loader.load(frame.url, tex => { tex.colorSpace = THREE.SRGBColorSpace; this.drawSampledFaces(); }, undefined, () => {});
        this.textures.set(frame.url, texture);
      }
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(6.4, 3.6), new THREE.MeshBasicMaterial({
        map: texture, side: THREE.DoubleSide, transparent: true,
        opacity: 0.14 + (frame === sampled[sampled.length - 1] ? 0.32 : 0), depthWrite: false,
      }));
      plane.position.z = this.z(frame.t);
      plane.userData.t = frame.t;
      this.content.add(plane);
      this.selectable.push(plane);
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(6.4, 3.6)), new THREE.LineBasicMaterial({ color: 0x8aa8b8, transparent: true, opacity: 0.16 }));
      edge.position.z = plane.position.z;
      this.content.add(edge);
    }
    // Topic lanes deliberately do not imply spatial object trajectories.
    const topics = new Map<string, number[]>();
    const common = new Set('the and with from this that there frame image background white black text screen visible shows left right bottom top small large dark synthetic test footage stage local live tube words column'.split(' '));
    for (const record of boundedRecords.filter(r => r.modality === 'video' || r.kind === 'scene')) {
      const extracted = Array.isArray(record.payload?.topics) ? record.payload.topics.filter((topic): topic is string => typeof topic === 'string') : [];
      const counts = new Map<string, number>();
      for (const word of (record.caption || record.text || '').toLowerCase().match(/[a-z]{4,}/g) || []) {
        if (!common.has(word)) counts.set(word, (counts.get(word) || 0) + 1);
      }
      const captionTerms = [...counts].sort((a, b) => b[1] - a[1] || b[0].length - a[0].length).slice(0, 3).map(([word]) => word);
      const words = extracted.length ? extracted : record.activity && record.activity !== 'visual_observation' ? [record.activity.replaceAll('_', ' ')] : record.entities?.length ? record.entities : captionTerms;
      for (const word of words.slice(0, 5)) {
        if (!word) continue;
        topics.set(word, [...(topics.get(word) || []), record.t_start]);
      }
    }
    const recurrent = [...topics.entries()].filter(([, times]) => times.length > 1).sort((a, b) => b[1].length - a[1].length).slice(0, 5);
    this.topicLabels = recurrent.map(([topic]) => topic);
    recurrent.forEach(([topic, times], lane) => {
      const x = -2.8 + lane * 1.35;
      const uniqueTimes = [...new Set(times)].sort((a, b) => a - b);
      const nodeTimes = uniqueTimes.length <= topicNodeLimit ? uniqueTimes : Array.from({ length: topicNodeLimit }, (_, i) => uniqueTimes[Math.round(i * (uniqueTimes.length - 1) / (topicNodeLimit - 1))]);
      const points = nodeTimes.map(t => new THREE.Vector3(x, -1.92 - lane * 0.035, this.z(t)));
      if (points.length < 2) return;
      const ribbon = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineBasicMaterial({ color: palette[lane], transparent: true, opacity: 0.82 }));
      ribbon.userData.topic = topic;
      this.content.add(ribbon);
      points.forEach((point, i) => {
        const node = new THREE.Mesh(new THREE.SphereGeometry(0.047, 8, 8), new THREE.MeshBasicMaterial({ color: palette[lane] }));
        node.position.copy(point);
        node.userData.t = nodeTimes[i];
        this.content.add(node);
        this.selectable.push(node);
      });
    });
    const activeUrls = new Set(sampled.map(f => f.url));
    for (const [url, texture] of this.textures) if (!activeUrls.has(url)) { texture.dispose(); this.textures.delete(url); }
    this.highlight(this.highlighted, this.highlightedSelection);
    this.drawSampledFaces();
    this.showTimeSlice();
  }

  highlight(citations: Evidence[], selected: number) {
    if (!this.valid) return;
    this.highlighted = citations;
    this.highlightedSelection = selected;
    this.clear(this.results);
    this.needsRender = true;
    citations.forEach((citation, i) => {
      if (!Number.isFinite(citation.t) || citation.t < 0 || citation.t > this.duration ||
          (citation.t_end !== undefined && (!Number.isFinite(citation.t_end) || citation.t_end > this.duration))) return;
      const start = Math.max(0, citation.t);
      const end = Math.min(this.duration, Math.max(start, citation.t_end ?? start));
      const depth = Math.max(0.05, Math.abs(this.z(end) - this.z(start)));
      const geometry = new THREE.BoxGeometry(6.5, 3.67, depth);
      const color = i === selected ? 0xb8f76c : 0xa596f4;
      const box = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: i === selected ? 0.16 : 0.045, depthWrite: false }));
      box.position.z = (this.z(start) + this.z(end)) / 2;
      this.results.add(box);
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({ color, transparent: true, opacity: i === selected ? 0.95 : 0.38 }));
      edge.position.copy(box.position);
      this.results.add(edge);
    });
  }

  setTime(t: number) { if (this.valid) { this.requestedTime = Math.max(0, Math.min(t, this.previewCutoff)); this.cursor.position.z = this.z(this.requestedTime); this.seekPreview(); } }
  setRotation(rotation: number) { if (this.valid) { this.volume.rotation.y = rotation; this.needsRender = true; } }
  reset() {
    if (!this.valid) return;
    this.autoFit = true;
    this.camera.position.set(10.4, 6.9, 13.8);
    this.controls.target.set(0, 0, 0);
    this.fitVolume();
  }
  zoom(direction: number) {
    if (!this.valid) return;
    this.autoFit = false;
    this.camera.position.sub(this.controls.target).multiplyScalar(direction > 0 ? 0.85 : 1.16).add(this.controls.target);
    this.controls.update();
    this.needsRender = true;
  }
}
