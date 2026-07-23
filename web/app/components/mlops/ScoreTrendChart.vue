<script setup lang="ts">
const props = defineProps<{ runs: GateRun[] }>()

const width = 600
const height = 160
const padding = 24
const threshold = 0.80

// `useGateHistory` renvoie les runs triés du plus récent au plus ancien
// (ORDER BY ran_at DESC côté API) — on les inverse pour tracer la courbe
// dans l'ordre chronologique (gauche = plus ancien).
const chronological = computed(() => [...props.runs].reverse())

const points = computed(() => {
  const n = chronological.value.length
  if (n === 0) return ''
  const stepX = n > 1 ? (width - 2 * padding) / (n - 1) : 0
  return chronological.value
    .map((run, i) => {
      const x = padding + i * stepX
      const y = height - padding - run.note_globale * (height - 2 * padding)
      return `${x},${y}`
    })
    .join(' ')
})

const thresholdY = height - padding - threshold * (height - 2 * padding)
</script>

<template>
  <UCard>
    <template #header>
      <span class="font-semibold">Tendance du score global</span>
    </template>

    <p
      v-if="runs.length === 0"
      class="text-sm text-muted"
    >
      Aucun run encore enregistré.
    </p>
    <svg
      v-else
      :viewBox="`0 0 ${width} ${height}`"
      class="w-full"
    >
      <line
        :x1="padding"
        :x2="width - padding"
        :y1="thresholdY"
        :y2="thresholdY"
        stroke="var(--ui-error, #ef4444)"
        stroke-dasharray="4 4"
      />
      <polyline
        :points="points"
        fill="none"
        stroke="var(--ui-primary, #00C16A)"
        stroke-width="2"
      />
    </svg>
    <p class="text-xs text-muted mt-1">
      Ligne pointillée : seuil de gate (80%).
    </p>
  </UCard>
</template>
