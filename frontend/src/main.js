import { createApp } from 'vue'
import App from './App.vue'
import '@fontsource-variable/inter'
import './styles.css'

if (window.location.pathname.replace(/\/+$/, '') === '/suggestions') {
  const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '/api/').replace(/\/+$/, '/')
  fetch(`${apiBaseUrl}public/suggestions-page`, { cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error(`Unable to load suggestions (${response.status})`)
      return response.text()
    })
    .then((html) => {
      html = html.replace("fetch('/api/public/signals'", `fetch('${apiBaseUrl}public/signals'`)
      document.open()
      document.write(html)
      document.close()
    })
    .catch((error) => {
      const root = document.querySelector('#app')
      root.textContent = error.message
      root.style.padding = '2rem'
    })
} else {
  createApp(App).mount('#app')
}
