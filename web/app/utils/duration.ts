export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`

  const totalSeconds = ms / 1000
  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(1).replace('.', ',')} s`
  }

  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.round(totalSeconds % 60)
  return `${minutes} min ${seconds} s`
}
