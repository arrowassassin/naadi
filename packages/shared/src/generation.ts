import { z } from "zod";

/** Payload of the `generated_media` canvas kind and of the assistant's `image` / `video` answer blocks (segment
 * 15b). Only the item id and kind travel; the client fetches the item so the signed URL is fresh. */
export const GeneratedMediaDataSchema = z.object({ media_id: z.uuid(), kind: z.enum(["image", "video"]) });
export type GeneratedMediaData = z.infer<typeof GeneratedMediaDataSchema>;
