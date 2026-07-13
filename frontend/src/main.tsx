import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles.css'
const client=new QueryClient({defaultOptions:{queries:{refetchInterval:30000,retry:1}}})
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><ConfigProvider locale={zhCN}><QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider></ConfigProvider></React.StrictMode>)
