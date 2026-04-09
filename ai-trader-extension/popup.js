const API_URL = "https://your-app.onrender.com/signal";

async function loadSignals() {
    try {
        const res = await fetch(API_URL);
        const data = await res.json();

        let html = "";

        data.all_signals.forEach(s => {
            html += `
                <div class="card">
                    <b>${s.symbol}</b><br>
                    Signal: ${s.signal}<br>
                    Price: ${s.price}<br>
                    SL: ${s.stop_loss ?? "-"}<br>
                    TP: ${s.take_profit ?? "-"}
                </div>
            `;
        });

        document.getElementById("signals").innerHTML = html;

    } catch (err) {
        document.getElementById("signals").innerText = "Error loading data";
    }
}

loadSignals();
setInterval(loadSignals, 10000);
