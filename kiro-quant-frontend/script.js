document.addEventListener('DOMContentLoaded', () => {
    // 1. Clock Update
    const timeElement = document.getElementById('current-time');
    setInterval(() => {
        const now = new Date();
        const timeString = now.toLocaleTimeString('zh-HK', { 
            hour12: false, 
            hour: '2-digit', 
            minute: '2-digit', 
            second: '2-digit' 
        });
        timeElement.textContent = timeString;
    }, 1000);

    // 2. Main K-Line Chart (00700.HK)
    const chartContainer = document.getElementById('main-chart');
    const bgSurface = '#1e293b'; // CSS var(--bg-surface-glass) approximation
    const textPrimary = '#f8fafc';
    const textSecondary = '#94a3b8';

    const chart = LightweightCharts.createChart(chartContainer, {
        width: chartContainer.clientWidth,
        height: 400,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: textSecondary,
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
        },
        timeScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
            timeVisible: true,
        },
    });

    const candlestickSeries = chart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderDownColor: '#ef4444',
        borderUpColor: '#10b981',
        wickDownColor: '#ef4444',
        wickUpColor: '#10b981',
    });

    // Generate dummy data for K-lines
    const generateData = () => {
        const data = [];
        let time = Math.floor(Date.now() / 1000) - 100 * 86400; // 100 days ago
        let open = 250;
        let high, low, close;

        for (let i = 0; i < 100; i++) {
            close = open + (Math.random() - 0.45) * 10;
            high = Math.max(open, close) + Math.random() * 5;
            low = Math.min(open, close) - Math.random() * 5;
            
            data.push({
                time: time,
                open: open,
                high: high,
                low: low,
                close: close
            });

            open = close;
            time += 86400; // Next day
        }
        return data;
    };

    const data = generateData();
    candlestickSeries.setData(data);

    // Handle Resize
    window.addEventListener('resize', () => {
        chart.applyOptions({ width: chartContainer.clientWidth });
    });

    // 3. Mini Sparkline Charts
    const createMiniChart = (containerId, color, dataPoints) => {
        const container = document.getElementById(containerId);
        const miniChart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 60,
            layout: {
                background: { type: 'solid', color: 'transparent' },
                textColor: 'transparent',
            },
            grid: {
                vertLines: { visible: false },
                horzLines: { visible: false },
            },
            timeScale: {
                visible: false,
            },
            rightPriceScale: {
                visible: false,
            },
            crosshair: {
                horzLine: { visible: false },
                vertLine: { visible: false },
            },
            handleScroll: false,
            handleScale: false,
        });

        const lineSeries = miniChart.addLineSeries({
            color: color,
            lineWidth: 2,
            crosshairMarkerVisible: false,
            lastValueVisible: false,
            priceLineVisible: false,
        });

        lineSeries.setData(dataPoints);
    };

    // Dummy data for mini charts
    const getMiniData = (trend = 'up') => {
        const data = [];
        let val = 100;
        let time = Math.floor(Date.now() / 1000) - 50 * 3600;
        for (let i = 0; i < 50; i++) {
            val += (Math.random() - (trend === 'up' ? 0.4 : 0.6)) * 2;
            data.push({ time: time, value: val });
            time += 3600;
        }
        return data;
    };

    createMiniChart('mini-chart-1', '#10b981', getMiniData('up')); // 00700
    createMiniChart('mini-chart-2', '#ef4444', getMiniData('down')); // AAPL
    createMiniChart('mini-chart-3', '#10b981', getMiniData('up')); // TSLA

    // Simulate Terminal Feed
    const terminalLines = [
        "[INFO] 正在監聽市場數據流...",
        "[EVENT] 檢測到板塊異動: 人工智能",
        "[UPDATE] AAPL.US 逐筆成交更新",
        "[SIGNAL] 00700.HK MACD 形成金叉，建議關注",
        "[INFO] 系統自動保存檢查點 (Checkpoint)"
    ];
    
    const terminalContent = document.getElementById('terminal-content');
    let lineIdx = 0;

    setInterval(() => {
        if (Math.random() > 0.7) {
            const newLine = document.createElement('div');
            newLine.textContent = terminalLines[lineIdx % terminalLines.length];
            terminalContent.appendChild(newLine);
            
            // Keep only latest 6 lines
            if(terminalContent.children.length > 6) {
                terminalContent.removeChild(terminalContent.firstChild);
            }
            lineIdx++;
        }
    }, 2000);

});
