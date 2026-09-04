const RAG_HISTORY_STORAGE_KEY_PREFIX='jobpulse:rag-history:';

export const LEGACY_RAG_CHAT_STORAGE_KEYS=['jobpulse.rag.chat.sessions.v1','jobpulse:rag-history'] as const;

export function ragHistoryStorageKey(userId:string){
  return RAG_HISTORY_STORAGE_KEY_PREFIX+userId;
}
