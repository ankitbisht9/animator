/**
 * Debug CLI around the same handlers the worker exposes.
 *   engine version
 */
import { handlers } from "./handlers";

function main(argv: string[]): number {
  const [cmd] = argv;
  switch (cmd) {
    case "version":
      process.stdout.write(`${JSON.stringify(handlers.version?.({}), null, 2)}\n`);
      return 0;
    default:
      process.stderr.write("usage: engine version\n");
      return 2;
  }
}

process.exit(main(process.argv.slice(2)));
