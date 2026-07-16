<script setup lang="ts">
const store = useChatSessionStore()

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 100)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const elapsedMs = computed(() => {
  if (!store.exchangeStartedAt) return 0
  return now.value - store.exchangeStartedAt
})

const steps = [
  { n: 1, label: 'Entrée' },
  { n: 2, label: 'Mémoire (lecture)' },
  { n: 3, label: 'Routage' },
  { n: 4, label: 'Sortie' },
  { n: 5, label: 'Mémoire (écriture)' },
  { n: 6, label: 'Résultat' }
]
</script>

<template>
  <div class="p-4 space-y-3">
    <p class="text-sm text-muted">
      Message envoyé — il chemine dans le pipeline de l'agent
      ({{ formatDuration(elapsedMs) }} écoulées)
    </p>

    <div class="relative">
      <div class="h-1 rounded-full bg-muted/20 overflow-hidden">
        <div class="pipeline-pulse h-1 w-1/4 rounded-full bg-primary" />
      </div>
      <div class="flex justify-between mt-2">
        <div
          v-for="s in steps"
          :key="s.n"
          class="flex flex-col items-center gap-1"
        >
          <UBadge
            variant="subtle"
            color="neutral"
            class="pipeline-dot"
            :style="{ animationDelay: `${(s.n - 1) * 0.35}s` }"
          >
            {{ s.n }}
          </UBadge>
          <span class="text-[10px] text-muted">{{ s.label }}</span>
        </div>
      </div>
    </div>

    <p class="text-xs text-muted">
      Chaque étape s'affichera ici dès que l'agent l'aura franchie : garde-fou
      d'entrée, lecture mémoire, routage, garde-fou de sortie, écriture
      mémoire, puis le résultat final.
    </p>
  </div>
</template>

<style scoped>
@keyframes pipeline-travel {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(400%); }
}
.pipeline-pulse {
  animation: pipeline-travel 2.2s ease-in-out infinite;
}

@keyframes pipeline-dot-pulse {
  0%, 100% { opacity: .45; }
  50% { opacity: 1; }
}
.pipeline-dot {
  animation: pipeline-dot-pulse 2.2s ease-in-out infinite;
}
</style>
