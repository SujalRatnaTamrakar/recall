import './style.css';

const view = new URLSearchParams(location.search).get('view');
if (view === 'moment') {
  void import('./moment');
} else if (['moderator', 'campaign', 'editor'].includes(view || '')) {
  void import('./review');
} else {
  void import('./main');
}
export {};
