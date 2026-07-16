<script setup lang="ts">
const store = useChatSessionStore()
const { data: customers } = useCustomers()
const userId = ref('C-marc-dubois')
const draft = ref('')

const customerItems = computed(() =>
  customers.value.map(c => ({ label: `${c.full_name} (${c.id})`, value: c.id }))
)

async function submit() {
  if (!draft.value.trim() || store.isStreaming) return
  const message = draft.value
  draft.value = ''
  await sendChatMessage(userId.value, message)
}

async function clear() {
  if (store.isStreaming) return
  await clearConversation(userId.value)
}
</script>

<template>
  <div class="relative flex flex-col h-full">
    <div class="flex-1 overflow-y-auto space-y-2 p-2">
      <div
        v-for="(m, i) in store.messages"
        :key="i"
        class="max-w-[80%] rounded-lg px-3 py-2 text-sm"
        :class="m.role === 'user'
          ? 'ml-auto bg-primary text-inverted'
          : 'mr-auto bg-muted/10 space-y-1'"
      >
        <span v-if="m.role === 'user'">{{ m.content }}</span>
        <!-- renderMarkdown() échappe tout le texte avant d'y insérer des balises fixes : aucun contenu brut n'est injecté -->
        <!-- eslint-disable vue/no-v-html -->
        <div
          v-else
          v-html="renderMarkdown(m.content)"
        />
        <!-- eslint-enable vue/no-v-html -->
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
      <USelect
        v-model="userId"
        :items="customerItems"
        class="w-56"
        placeholder="Sélectionner un client"
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

    <UButton
      type="button"
      class="absolute top-2 right-2"
      color="neutral"
      variant="ghost"
      size="xs"
      icon="i-lucide-eraser"
      :disabled="store.isStreaming"
      title="Effacer la conversation"
      @click="clear"
    />
  </div>
</template>
