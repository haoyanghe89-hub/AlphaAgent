import { createApp } from 'vue'
import App from './App.vue'
import { useTheme } from './composables/useTheme'

import './styles/alpha.css'
import './styles/daily-ui.css'

useTheme()

createApp(App).mount('#app')
