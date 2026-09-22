import FingerprintJS from "@fingerprintjs/fingerprintjs";

export async function getDeviceFingerprint() {
  const fp = await FingerprintJS.load();
  const result = await fp.get();
  return result.visitorId;
}
