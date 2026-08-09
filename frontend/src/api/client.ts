export class ApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly retryable: boolean,
    public readonly status: number,
  ) {
    super(code);
  }
}

export async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = init === undefined ? await fetch(input) : await fetch(input, init);
  } catch {
    throw new ApiError("BACKEND_UNAVAILABLE", true, 0);
  }
  if (response.ok) return response.json() as Promise<T>;

  let error: { error?: { code?: string; retryable?: boolean } } = {};
  try {
    error = await response.json() as typeof error;
  } catch {
    // An unavailable local backend can return an HTML or empty response.
  }
  throw new ApiError(
    error.error?.code ?? `HTTP_${response.status}`,
    Boolean(error.error?.retryable) || response.status >= 500,
    response.status,
  );
}

export const userMessageForError = (code: string): string => {
  const messages: Record<string, string> = {
    BACKEND_UNAVAILABLE: "Psych-local n’arrive plus à joindre son backend local.",
    LLM_BACKEND_UNAVAILABLE: "Le moteur local n’est pas disponible.",
    LLM_GENERATION_FAILED: "La réponse a été interrompue par une erreur.",
    PROCESS_INTERRUPTED: "La réponse a été interrompue par une erreur.",
    CONVERSATION_BUSY: "Cette conversation génère déjà une réponse.",
    DATABASE_ERROR: "Psych-local n’a pas pu enregistrer cette opération.",
    CONVERSATION_NOT_FOUND: "Cette conversation n’existe plus.",
  };
  return messages[code] ?? "Une erreur locale est survenue. Vous pouvez réessayer.";
};
