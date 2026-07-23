<script setup lang="ts">
useSeoMeta({
  title: 'Velmo — Gate qualité MLOps',
  description: 'Déclenche le gate qualité MLOps et suit sa progression en direct.'
})

const store = useMlopsGateStore()
const { data: runs, refresh } = useGateHistory()
const selectedRunId = ref<string | null>(null)
const detailOpen = ref(false)

watch(() => store.lastResult, (result) => {
  if (result) refresh()
})

function onSelectRun(runId: string) {
  selectedRunId.value = runId
  detailOpen.value = true
}
</script>

<template>
  <div class="p-4 space-y-4 max-w-3xl mx-auto">
    <GatePanel />
    <HistoryTable
      :runs="runs"
      @select="onSelectRun"
    />
    <ScoreTrendChart :runs="runs" />
    <GateRunDetail
      v-model:open="detailOpen"
      :run-id="selectedRunId"
    />
  </div>
</template>
