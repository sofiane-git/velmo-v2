function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderInline(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+?)`/g, '<code class="px-1 py-0.5 rounded bg-muted/20 text-xs">$1</code>')
}

/**
 * Convertit le sous-ensemble de Markdown produit par le LLM (titres, listes,
 * gras/italique, code inline, séparateurs) en HTML. Le texte est toujours
 * échappé avant insertion : seules les balises générées ici (jamais du
 * contenu brut) finissent dans le HTML rendu, donc pas d'injection possible
 * via la réponse du modèle.
 */
export function renderMarkdown(text: string): string {
  const html: string[] = []
  let list: { tag: 'ul' | 'ol', items: string[] } | null = null

  const flushList = () => {
    if (!list) return
    const cls = list.tag === 'ul' ? 'list-disc' : 'list-decimal'
    html.push(`<${list.tag} class="${cls} pl-5 space-y-0.5">`)
    for (const item of list.items) html.push(`<li>${renderInline(item)}</li>`)
    html.push(`</${list.tag}>`)
    list = null
  }

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) {
      flushList()
      continue
    }
    if (/^-{3,}$/.test(line)) {
      flushList()
      html.push('<hr class="my-2 border-default">')
      continue
    }
    const heading = line.match(/^#{1,4}\s+(.*)$/)
    if (heading) {
      flushList()
      html.push(`<p class="font-semibold mt-2 first:mt-0">${renderInline(heading[1] ?? '')}</p>`)
      continue
    }
    const bullet = line.match(/^[-*]\s+(.*)$/)
    if (bullet) {
      if (!list || list.tag !== 'ul') {
        flushList()
        list = { tag: 'ul', items: [] }
      }
      list.items.push(bullet[1] ?? '')
      continue
    }
    const numbered = line.match(/^\d+[.)]\s+(.*)$/)
    if (numbered) {
      if (!list || list.tag !== 'ol') {
        flushList()
        list = { tag: 'ol', items: [] }
      }
      list.items.push(numbered[1] ?? '')
      continue
    }
    flushList()
    html.push(`<p>${renderInline(line)}</p>`)
  }
  flushList()
  return html.join('')
}
