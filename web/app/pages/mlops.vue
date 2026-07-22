<script setup lang="ts">
useSeoMeta({
  title: 'Velmo — Gate qualité MLOps',
  description: 'Déclenche le gate qualité MLOps et suit sa progression en direct.'
})
</script>

<template>
  <div class="p-4 space-y-4 max-w-3xl mx-auto">
    <GatePanel />
  </div>
</template>
