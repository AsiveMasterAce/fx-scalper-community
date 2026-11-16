const SAST_TIME_ZONE = 'Africa/Johannesburg'

export function formatSastDateTime(value) {
  if (!value) return 'Not yet'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return `${new Intl.DateTimeFormat('en-ZA', {
    timeZone: SAST_TIME_ZONE,
    dateStyle: 'medium',
    timeStyle: 'medium',
    hour12: false,
  }).format(date)} SAST`
}

export function formatSastTime(value) {
  if (!value) return '–'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return `${new Intl.DateTimeFormat('en-ZA', {
    timeZone: SAST_TIME_ZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)} SAST`
}
