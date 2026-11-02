import './models.css';

interface ModelChoice { name: string; size_bytes: number; compatible: boolean; reason: string }
interface ModelList { selected: string | null; models: ModelChoice[]; error?: string }

export function installModelSettings(request: <T>(path: string, data?: unknown) => Promise<T>) {
  const panel = document.createElement('details');
  panel.className = 'diagnostics-panel model-settings';
  panel.id = 'modelSettings';
  panel.innerHTML = `<summary><span>Local models <small>Choose video &amp; answer model</small></span></summary><div class="diagnostics-body"><p>Speech uses Whisper on the CPU while video analysis uses Ollama on the GPU when the model fits. Both feed the same timestamped memory.</p><label for="localModel">Installed Ollama model for video and answers</label><div class="diagnostics-actions"><select id="localModel" aria-describedby="modelHelp"><option>Open to load models…</option></select><button class="button" id="applyLocalModel" disabled>Use model</button><button class="button" id="refreshLocalModels">Refresh list</button></div><p id="modelHelp">Pause playback and let frame analysis finish before switching. Models without vision support and cloud models are unavailable here. Larger models may spill into RAM and run more slowly.</p><p id="modelFeedback" role="status"></p></div>`;
  document.querySelector('#diagnosticsPanel')!.before(panel);
  const select = panel.querySelector<HTMLSelectElement>('#localModel')!;
  const apply = panel.querySelector<HTMLButtonElement>('#applyLocalModel')!;
  const refresh = panel.querySelector<HTMLButtonElement>('#refreshLocalModels')!;
  const feedback = panel.querySelector<HTMLElement>('#modelFeedback')!;
  let choices: ModelChoice[] = [];
  let busy = false;
  const enable = () => { apply.disabled = busy || !choices.some(model => model.name === select.value && model.compatible); };
  async function load() {
    if (busy) return;
    busy = true; refresh.disabled = true; enable();
    feedback.textContent = 'Reading installed models…';
    try {
      const result = await request<ModelList>('/api/models');
      choices = result.models || [];
      select.replaceChildren();
      for (const model of choices) {
        const option = document.createElement('option');
        option.value = model.name;
        option.textContent = `${model.name} · ${(model.size_bytes / 1073741824).toFixed(1)} GB · ${model.reason}`;
        option.disabled = !model.compatible;
        select.append(option);
      }
      if (result.selected) select.value = result.selected;
      feedback.textContent = result.error || (choices.length ? `Selected: ${result.selected || 'none'}. Selection applies to this session; existing memory is preserved.` : 'No local models found. Start Recall with local AI enabled, then refresh.');
    } catch (error) { feedback.textContent = error instanceof Error ? error.message : 'Could not load models.'; }
    finally { busy = false; refresh.disabled = false; enable(); }
  }
  apply.addEventListener('click', async () => {
    if (busy) return;
    busy = true; enable(); feedback.textContent = 'Checking model…';
    try {
      const result = await request<{selected:string}>('/api/model', {model:select.value});
      feedback.textContent = `Using ${result.selected}. Existing memory is preserved. The first request may take longer while the model loads.`;
    } catch (error) { feedback.textContent = error instanceof Error ? error.message : 'Could not select the model.'; }
    finally { busy = false; enable(); }
  });
  select.addEventListener('change',enable);
  refresh.addEventListener('click',() => void load());
  panel.addEventListener('toggle',() => { if (panel.open) void load(); });
}
