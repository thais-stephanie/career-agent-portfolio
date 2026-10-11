/**
 * autoupdate-notice.js -- what an automatic update found, said once.
 *
 * It never reloads anything: the list, the open posting and the scroll position
 * stay as they are until the person presses the button.
 */

import { button } from './dom.js';
import { toast } from './ui.js';
import { t } from './i18n.js';

export function announceNewJobs(count, updateList) {
  const n = Number(count) || 0;
  if (n <= 0) return;
  toast(t(n === 1 ? 'auto.newOne' : 'auto.newMany', { n }), {
    extras: [button(t('auto.updateList'), () => updateList(), { className: 'toast__undo' })],
    timeout: 30000,
  });
}
