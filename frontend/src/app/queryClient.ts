import { QueryClient } from "@tanstack/react-query";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 1_000,
        retry: (failureCount, error) => {
          const status =
            typeof error === "object" && error !== null && "status" in error
              ? Number(error.status)
              : 0;
          return failureCount < 2 && (status === 0 || status >= 500);
        },
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}
