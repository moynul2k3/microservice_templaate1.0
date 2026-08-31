importScripts("https://www.gstatic.com/firebasejs/12.13.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/12.13.0/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyD_xfQ_jL7UYbpnuxluvX5glmOrp1dziIY",
  authDomain: "test-v1-fbde6.firebaseapp.com",
  projectId: "test-v1-fbde6",
  storageBucket: "test-v1-fbde6.firebasestorage.app",
  messagingSenderId: "192440466238",
  appId: "1:192440466238:web:e04aaa2732a500cda5ca6f",
  measurementId: "G-N9FL2XTCJF"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {
  const notificationTitle = payload?.notification?.title || payload?.data?.title || "Notification";
  const notificationOptions = {
    body: payload?.notification?.body || payload?.data?.body || "",
    data: payload?.data || {}
  };

  self.registration.showNotification(notificationTitle, notificationOptions);
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const href = event.notification?.data?.href || "/";
  event.waitUntil(clients.openWindow(href));
});
