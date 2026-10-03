/**
 * caution.js -- "This ad may be a scam": which signs to show for a posting.
 *
 * The signs come from the server (`job.caution`, read from the whole ad by
 * web/caution.py). This module only remembers "I checked. It looks fine",
 * per local profile and per posting, on this computer. It changes nothing
 * about the posting itself and nothing about any score.
 */

import { getLocalProfile } from './api.js';

const KEY = 'careerAgent.cautionChecked.v1';

function read() {
  try {
    return JSON.parse(window.localStorage.getItem(KEY) || '{}') || {};
  } catch {
    return {};
  }
}

const who = () => getLocalProfile() || 'default';

/** The signs to show for this posting: none once this profile checked it. */
export function cautionOf(job) {
  const signals = (job && job.caution) || [];
  if (!signals.length) return [];
  return (read()[who()] || []).includes(job.job_id) ? [] : signals;
}

/** "I checked. It looks fine", for this profile only. The newest 500 are kept. */
export function markChecked(jobId) {
  const all = read();
  const mine = new Set(all[who()] || []);
  mine.add(jobId);
  all[who()] = [...mine].slice(-500);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // Without storage the warning simply shows again next time.
  }
}
