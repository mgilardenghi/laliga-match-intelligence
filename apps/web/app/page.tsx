/**
 * The home page, still the M0 placeholder until the table page lands.
 *
 * The shell owns the `main` landmark and the API status line now, so this page
 * renders neither: a second `main` element would be invalid HTML and a second
 * "API status:" line would leave two elements answering the same question.
 */
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

export default function Home() {
  return (
    <div className={styles.main}>
      <h1 className={styles.title}>LaLiga Match Intelligence</h1>
      <p className={styles.description}>
        Predicts La Liga matches, simulates the rest of the season and measures
        its own accuracy against real results and the betting market.
      </p>
    </div>
  );
}
