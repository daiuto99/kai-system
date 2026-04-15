export const ADVISORS = [
  {
    id:      'kai',
    channel: 'chief',
    name:    'KAI',
    role:    'Chief of Staff',
    emoji:   '⚡',
    color:   '#3882F6',
    intro:   'Your chief of staff. Always in the room.',
  },
  {
    id:      'ember',
    channel: 'ember',
    name:    'Ember',
    role:    'Emotional & personal',
    emoji:   '🔥',
    color:   '#F43F5E',
    intro:   'Warm, direct, deeply present. Patterns, insight, growth.',
  },
  {
    id:      'beats',
    channel: 'beats',
    name:    'Beats',
    role:    'Music & creative',
    emoji:   '🎵',
    color:   '#F97316',
    intro:   'Your music director. Studio, tone, creative direction.',
  },
  {
    id:      'doc',
    channel: 'doc',
    name:    'Doc',
    role:    'Health & longevity',
    emoji:   '💊',
    color:   '#10B981',
    intro:   'Longevity, optimization, everyday wellness.',
  },
  {
    id:      'coach',
    channel: 'coach',
    name:    'Coach',
    role:    'Performance & fitness',
    emoji:   '💪',
    color:   '#EAB308',
    intro:   'Training, nutrition, mental performance.',
  },
  {
    id:      'biz',
    channel: 'biz',
    name:    'Biz',
    role:    'Business & strategy',
    emoji:   '📊',
    color:   '#A855F7',
    intro:   'Strategy, finance, business decisions.',
  },
]

export function getAdvisor(id) {
  return ADVISORS.find(a => a.id === id) || ADVISORS[0]
}
