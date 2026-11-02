import './perspectives.css';

export function perspectiveNav(active: string) {
  const views = [['studio', '/', 'Studio'], ['demo', '/?view=demo', 'Viewer'],
    ['moderator', '/?view=moderator', 'Moderator'], ['campaign', '/?view=campaign', 'Campaign'],
    ['editor', '/?view=editor', 'Editor'], ['replay', '/broadcast', 'Local replay']];
  // Separate windows preserve the source player's clock while reviewing history.
  return `<nav class="perspective-nav" aria-label="Perspectives"><span>WORKSPACE</span>${views.map(([id, href, label]) =>
    `<a href="${href}" target="${id === 'demo' || id === 'replay' ? 'recall-replay' : `recall-${id}`}" ${id === active ? 'aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
}
