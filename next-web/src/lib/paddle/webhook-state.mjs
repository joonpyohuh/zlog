export function shouldProcessWebhook(existing) {
  return !existing || Boolean(existing.processing_error);
}
