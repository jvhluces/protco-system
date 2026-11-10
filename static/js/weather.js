document.addEventListener("DOMContentLoaded", () => {
    // Real-time Clock (Malaking Hours:Minutes, Maliit na Seconds & AM/PM)
    function updateClock() {
        const now = new Date();
        let hours = now.getHours();
        const minutes = String(now.getMinutes()).padStart(2, '0');
        const seconds = String(now.getSeconds()).padStart(2, '0');
        const ampm = hours >= 12 ? 'PM' : 'AM';
        
        hours = hours % 12;
        hours = hours ? hours : 12;
        const formattedHours = String(hours).padStart(2, '0');

        const mainTime = `${formattedHours}:${minutes}`;
        const secTime = `${seconds} ${ampm}`;

        const mainEl = document.getElementById("clock-main");
        const secEl = document.getElementById("clock-sec");
        
        if (mainEl && secEl) {
            mainEl.innerText = mainTime;
            secEl.innerText = secTime;
        }
    }

    updateClock();
    setInterval(updateClock, 1000);

    // Weather API
    const apiKey = "b7bc9b6e96ab167c8ade3987e17a8643"; // Palitan kung kinakailangan
    const city = "Manila";
    const url = `https://api.openweathermap.org/data/2.5/weather?q=${city}&units=metric&appid=${apiKey}`;

    fetch(url)
        .then(response => response.json())
        .then(data => {
            if (data.cod === 200) {
                document.getElementById("temperature").innerText = Math.round(data.main.temp) + "°C";
                document.getElementById("weather-description").innerText = data.weather[0].description;
                document.getElementById("feels-like").innerText = Math.round(data.main.feels_like) + "°C";
                document.getElementById("humidity").innerText = data.main.humidity + "%";
                document.getElementById("wind-speed").innerText = data.wind.speed + " m/s";
                
                const iconCode = data.weather[0].icon;
                document.getElementById("weather-icon").src = `https://openweathermap.org/img/wn/${iconCode}@4x.png`;
            } else {
                document.getElementById("weather-description").innerText = "Data unavailable";
            }
        })
        .catch(error => {
            console.error("Error fetching weather data:", error);
            document.getElementById("weather-description").innerText = "Connection error";
        });
});