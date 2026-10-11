/**
 * profile-scope.js -- browser memory that belongs to ONE local profile.
 *
 * Every profile of this install is served from the same address, so the
 * browser's storage is shared by all of them. A choice that describes a
 * PERSON (the setup they postponed, the card they left it on, the words they
 * hid, the notices they closed) must not travel to the next person created on
 * this computer: a second profile opened with the first one's "do this later"
 * and never saw its own onboarding.
 *
 * `scopedKey` adds the active profile's id to a key. The id is known before
 * anything reads storage (`api.bootProfile` runs first in main.js). Without
 * local profiles (the demo, `serve`) the key is unchanged. Display
 * preferences of the person at the keyboard, not of a profile (theme,
 * language, table columns), keep their plain keys.
 */

import { getLocalProfile } from './api.js';

export function scopedKey(key) {
  const id = getLocalProfile();
  return id ? `${key}@${id}` : key;
}
