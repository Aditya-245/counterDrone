import { useQuery } from "@tanstack/react-query"
import { api } from "@/api/client"

/** Compact backend health indicator for the top bar. */
export default function HealthPill(): JSX.Element {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 30_000,
    staleTime: 10_000,
  })

  const ok = !isError && data?.status === "ok"
  const tone = isError ? "crit" : ok ? "ok" : "warn"
  const text = isError
    ? "backend offline"
    : ok
      ? `${data?.tick_hz ?? 10} Hz · v${data?.version ?? "?"}`
      : "degraded"

  return (
    <span
      className={`badge ${tone}`}
      title={data ? JSON.stringify(data.learned_policy) : undefined}
    >
      {text}
    </span>
  )
}
