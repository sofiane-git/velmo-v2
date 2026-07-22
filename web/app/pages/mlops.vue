<script setup lang="ts">
useSeoMeta({
  title: 'Velmo — Gate qualité MLOps',
  description: 'Déclenche le gate qualité MLOps et suit sa progression en direct.'
})

const store = useMlopsGateStore()
const { data: runs, refresh } = useGateHistory()

watch(() => store.lastResult, (result) => {
  if (result) refresh()
})
</script>

<template>
  <div class="p-4 space-y-4 max-w-3xl mx-auto">
    <GatePanel />
    <HistoryTable :runs="runs" />
  </div>
</template>
