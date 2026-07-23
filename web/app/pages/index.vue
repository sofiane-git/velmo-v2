<script setup lang="ts">
const recapOpen = ref(false)
</script>

<template>
  <div class="flex flex-col h-[calc(100vh-4rem)]">
    <div class="flex justify-end p-2 border-b border-default">
      <UButton
        variant="subtle"
        @click="recapOpen = true"
      >
        Voir le bilan
      </UButton>
    </div>

    <div class="flex flex-1 min-h-0">
      <div class="w-1/2 border-r border-default min-h-0">
        <ChatPanel />
      </div>
      <div class="w-1/2 min-h-0">
        <TracePanel />
      </div>
    </div>

    <SessionRecap v-model:open="recapOpen" />
  </div>
</template>
