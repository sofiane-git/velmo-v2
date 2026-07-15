<script setup lang="ts">
const store = useChatSessionStore()
const userId = ref('C-marc-dubois')
const draft = ref('')

async function submit() {
  if (!draft.value.trim() || store.isStreaming) return
  const message = draft.value
  draft.value = ''
  await sendChatMessage(userId.value, message)
}
</script>

<template>
  <div class="flex flex-col h-full">
    <div class="flex-1 overflow-y-auto space-y-2 p-2">
      <div
        v-for="(m, i) in store.messages"
        :key="i"
        class="max-w-[80%] rounded-lg px-3 py-2 text-sm"
        :class="m.role === 'user'
          ? 'ml-auto bg-primary text-inverted'
          : 'mr-auto bg-muted/10'"
      >
        {{ m.content }}
      </div>
      <p
        v-if="store.isStreaming"
        class="text-xs text-muted italic"
      >
        L'agent réfléchit…
      </p>
    </div>

    <ScenarioPicker />

    <form
      class="flex gap-2 p-2 border-t border-default"
      @submit.prevent="submit"
    >
      <UInput
        v-model="userId"
        class="w-40"
        placeholder="user_id"
      />
      <UInput
        v-model="draft"
        class="flex-1"
        placeholder="Écrire un message…"
      />
      <UButton
        type="submit"
        :disabled="store.isStreaming"
      >
        Envoyer
      </UButton>
    </form>
  </div>
</template>
