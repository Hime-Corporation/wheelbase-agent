/*
 * cn is the installer's Tailwind-aware class merger. It handles conditional
 * classes and resolves utility conflicts, so
 * `cn('px-2', condition && 'px-4')` ends up with px-4 only, not both.
 */
export { cn } from 'cn'
