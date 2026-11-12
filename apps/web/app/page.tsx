import { getApiStatus } from "@/lib/api";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

export default async function Home() {
  const apiStatus = await getApiStatus();

  return (
    <main className={styles.main}>
      <h1 className={styles.title}>LaLiga Match Intelligence</h1>
      <p className={styles.description}>
        Predicts La Liga matches, simulates the rest of the season and measures
        its own accuracy against real results and the betting market.
      </p>
      <p className={styles.status}>
        API status:{" "}
        <span
          className={
            apiStatus === "online" ? styles.statusOnline : styles.statusOffline
          }
        >
          {apiStatus}
        </span>
      </p>
    </main>
  );
}
