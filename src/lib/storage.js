function normalizeKeys(keys) {
  if (Array.isArray(keys)) {
    return keys;
  }

  return [keys];
}

export const storage = {
  async get(key, fallbackValue = null) {
    const result = await chrome.storage.local.get(key);
    return result[key] ?? fallbackValue;
  },

  async getMany(keys) {
    const normalizedKeys = normalizeKeys(keys);
    return chrome.storage.local.get(normalizedKeys);
  },

  async set(key, value) {
    await chrome.storage.local.set({ [key]: value });
  },

  async setMany(values) {
    await chrome.storage.local.set(values);
  },

  async remove(keys) {
    await chrome.storage.local.remove(normalizeKeys(keys));
  },

  async clear() {
    await chrome.storage.local.clear();
  }
};
